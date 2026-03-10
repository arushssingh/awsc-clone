import json
from typing import Optional

import docker
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_permission
from database import VPC, Instance, User, get_db, generate_id

router = APIRouter(prefix="/api/v1/vpc", tags=["vpc"])

_docker_client = None

def get_docker():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


# ── Schemas ──────────────────────────────────────────────────────────────

class CreateVPCRequest(BaseModel):
    name: str
    cidr_block: str = "10.0.0.0/16"


class VPCResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    docker_network_id: Optional[str]
    cidr_block: str
    state: str
    created_at: str


class VPCDetailResponse(VPCResponse):
    instances: list[dict]


class AttachRequest(BaseModel):
    instance_id: str


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/vpcs")
async def list_vpcs(
    user: User = Depends(require_permission("vpc:DescribeVpcs")),
    db: AsyncSession = Depends(get_db),
):
    if user.is_root:
        result = await db.execute(select(VPC).where(VPC.state != "deleting"))
    else:
        result = await db.execute(
            select(VPC).where(VPC.owner_id == user.id, VPC.state != "deleting")
        )
    vpcs = result.scalars().all()
    return [_vpc_to_dict(v) for v in vpcs]


@router.get("/vpcs/{vpc_id}")
async def get_vpc(
    vpc_id: str,
    user: User = Depends(require_permission("vpc:DescribeVpcs")),
    db: AsyncSession = Depends(get_db),
):
    vpc = await _get_vpc(vpc_id, db)
    # Get connected instances
    result = await db.execute(
        select(Instance).where(Instance.vpc_id == vpc_id, Instance.state != "terminated")
    )
    instances = result.scalars().all()
    resp = _vpc_to_dict(vpc)
    resp["instances"] = [
        {
            "id": i.id,
            "name": i.name,
            "state": i.state,
            "private_ip": i.private_ip,
            "instance_type": i.instance_type,
        }
        for i in instances
    ]
    return resp


@router.post("/vpcs")
async def create_vpc(
    body: CreateVPCRequest,
    user: User = Depends(require_permission("vpc:CreateVpc")),
    db: AsyncSession = Depends(get_db),
):
    vpc_id = generate_id()

    try:
        ipam_pool = docker.types.IPAMPool(subnet=body.cidr_block)
        ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool])
        network = get_docker().networks.create(
            name=f"awsclone-vpc-{vpc_id}",
            driver="bridge",
            ipam=ipam_config,
            labels={"awsclone": "true", "vpc_id": vpc_id},
        )
    except docker.errors.APIError as e:
        raise HTTPException(status_code=400, detail=f"Failed to create network: {e.explanation}")

    vpc = VPC(
        id=vpc_id,
        name=body.name,
        owner_id=user.id,
        docker_network_id=network.id,
        cidr_block=body.cidr_block,
        state="available",
    )
    db.add(vpc)
    await db.flush()
    return _vpc_to_dict(vpc)


@router.delete("/vpcs/{vpc_id}")
async def delete_vpc(
    vpc_id: str,
    user: User = Depends(require_permission("vpc:DeleteVpc")),
    db: AsyncSession = Depends(get_db),
):
    vpc = await _get_vpc(vpc_id, db)

    # Check no instances are connected
    result = await db.execute(
        select(Instance).where(Instance.vpc_id == vpc_id, Instance.state != "terminated")
    )
    if result.first():
        raise HTTPException(status_code=400, detail="VPC has connected instances. Remove them first.")

    # Remove Docker network
    if vpc.docker_network_id:
        try:
            network = get_docker().networks.get(vpc.docker_network_id)
            network.remove()
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            raise HTTPException(status_code=500, detail=f"Failed to remove network: {e.explanation}")

    await db.delete(vpc)
    return {"detail": "VPC deleted"}


@router.post("/vpcs/{vpc_id}/attach")
async def attach_instance(
    vpc_id: str,
    body: AttachRequest,
    user: User = Depends(require_permission("vpc:AttachInstance")),
    db: AsyncSession = Depends(get_db),
):
    vpc = await _get_vpc(vpc_id, db)
    result = await db.execute(select(Instance).where(Instance.id == body.instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    if instance.state != "running":
        raise HTTPException(status_code=400, detail="Instance must be running to attach")

    # Connect Docker container to network
    if vpc.docker_network_id and instance.docker_container_id:
        try:
            network = get_docker().networks.get(vpc.docker_network_id)
            network.connect(instance.docker_container_id)
        except docker.errors.APIError as e:
            raise HTTPException(status_code=500, detail=f"Failed to attach: {e.explanation}")

    instance.vpc_id = vpc_id
    # Get the private IP from Docker
    if instance.docker_container_id:
        try:
            container = get_docker().containers.get(instance.docker_container_id)
            container.reload()
            nets = container.attrs["NetworkSettings"]["Networks"]
            net_name = f"awsclone-vpc-{vpc_id}"
            if net_name in nets:
                instance.private_ip = nets[net_name]["IPAddress"]
        except Exception:
            pass

    await db.flush()
    return {"detail": "Instance attached to VPC", "private_ip": instance.private_ip}


@router.post("/vpcs/{vpc_id}/detach")
async def detach_instance(
    vpc_id: str,
    body: AttachRequest,
    user: User = Depends(require_permission("vpc:DetachInstance")),
    db: AsyncSession = Depends(get_db),
):
    vpc = await _get_vpc(vpc_id, db)
    result = await db.execute(select(Instance).where(Instance.id == body.instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Disconnect Docker container from network
    if vpc.docker_network_id and instance.docker_container_id:
        try:
            network = get_docker().networks.get(vpc.docker_network_id)
            network.disconnect(instance.docker_container_id)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError:
            pass

    instance.vpc_id = None
    instance.private_ip = None
    await db.flush()
    return {"detail": "Instance detached from VPC"}


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_vpc(vpc_id: str, db: AsyncSession) -> VPC:
    result = await db.execute(select(VPC).where(VPC.id == vpc_id))
    vpc = result.scalar_one_or_none()
    if not vpc:
        raise HTTPException(status_code=404, detail="VPC not found")
    return vpc


def _vpc_to_dict(vpc: VPC) -> dict:
    return {
        "id": vpc.id,
        "name": vpc.name,
        "owner_id": vpc.owner_id,
        "docker_network_id": vpc.docker_network_id,
        "cidr_block": vpc.cidr_block,
        "state": vpc.state,
        "created_at": vpc.created_at.isoformat(),
    }
