import json
import threading
from typing import Optional

import docker
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_permission
from config import EC2_PORT_RANGE_START, EC2_PORT_RANGE_END
from database import Instance, VPC, User, get_db, generate_id

router = APIRouter(prefix="/api/v1/ec2", tags=["ec2"])

# Lazy Docker client — only connects when first used (not at import time)
_docker_client = None

def get_docker():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client

# Lock for port allocation
_port_lock = threading.Lock()
_next_port = EC2_PORT_RANGE_START

# ── Instance types ───────────────────────────────────────────────────────

INSTANCE_TYPES = {
    "t2.nano":   {"cpu": 0.25, "memory": 128,  "nano_cpus": 250_000_000, "mem_limit": "128m"},
    "t2.micro":  {"cpu": 0.5,  "memory": 256,  "nano_cpus": 500_000_000, "mem_limit": "256m"},
    "t2.small":  {"cpu": 1.0,  "memory": 512,  "nano_cpus": 1_000_000_000, "mem_limit": "512m"},
    "t2.medium": {"cpu": 1.0,  "memory": 1024, "nano_cpus": 1_000_000_000, "mem_limit": "1024m"},
}


# ── Schemas ──────────────────────────────────────────────────────────────

class LaunchInstanceRequest(BaseModel):
    name: str
    image: str = "nginx:alpine"
    instance_type: str = "t2.micro"
    vpc_id: Optional[str] = None
    port_mappings: Optional[dict] = None   # {"80": 0} — 0 means auto-assign
    environment: Optional[dict] = None
    command: Optional[str] = None


class InstanceResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    docker_container_id: Optional[str]
    image: str
    instance_type: str
    state: str
    vpc_id: Optional[str]
    private_ip: Optional[str]
    port_mappings: Optional[dict]
    cpu_limit: float
    memory_limit: int
    created_at: str
    updated_at: str


# ── Port allocation ──────────────────────────────────────────────────────

def _allocate_port() -> int:
    """Allocate the next available host port."""
    global _next_port
    with _port_lock:
        port = _next_port
        _next_port += 1
        if _next_port > EC2_PORT_RANGE_END:
            _next_port = EC2_PORT_RANGE_START
        return port


def _build_port_bindings(port_mappings: dict) -> dict:
    """
    Convert user port mappings to Docker format.
    Input:  {"80": 0, "443": 8443}
    Output: {"80/tcp": 49152, "443/tcp": 8443}
    Also returns the resolved mappings for DB storage.
    """
    bindings = {}
    resolved = {}
    for container_port, host_port in port_mappings.items():
        cp = str(container_port)
        hp = int(host_port) if int(host_port) > 0 else _allocate_port()
        bindings[f"{cp}/tcp"] = hp
        resolved[cp] = hp
    return bindings, resolved


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/instance-types")
async def list_instance_types():
    return [
        {"name": k, "cpu": v["cpu"], "memory": v["memory"]}
        for k, v in INSTANCE_TYPES.items()
    ]


@router.get("/instances")
async def list_instances(
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    if user.is_root:
        result = await db.execute(
            select(Instance).where(Instance.state != "terminated")
        )
    else:
        result = await db.execute(
            select(Instance).where(
                Instance.owner_id == user.id, Instance.state != "terminated"
            )
        )
    instances = result.scalars().all()
    return [_instance_to_dict(i) for i in instances]


@router.get("/instances/{instance_id}")
async def get_instance(
    instance_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    return _instance_to_dict(instance)


@router.post("/instances")
async def launch_instance(
    body: LaunchInstanceRequest,
    user: User = Depends(require_permission("ec2:RunInstance")),
    db: AsyncSession = Depends(get_db),
):
    # Validate instance type
    itype = INSTANCE_TYPES.get(body.instance_type)
    if not itype:
        raise HTTPException(status_code=400, detail=f"Invalid instance type: {body.instance_type}")

    instance_id = generate_id()

    # Build port bindings
    port_bindings = {}
    resolved_ports = {}
    if body.port_mappings:
        port_bindings, resolved_ports = _build_port_bindings(body.port_mappings)

    # Determine Docker network
    network_name = None
    if body.vpc_id:
        result = await db.execute(select(VPC).where(VPC.id == body.vpc_id))
        vpc = result.scalar_one_or_none()
        if not vpc:
            raise HTTPException(status_code=404, detail="VPC not found")
        network_name = f"awsclone-vpc-{vpc.id}"

    # Launch container
    try:
        run_kwargs = {
            "image": body.image,
            "name": f"awsclone-{instance_id}",
            "detach": True,
            "nano_cpus": itype["nano_cpus"],
            "mem_limit": itype["mem_limit"],
            "environment": body.environment or {},
            "labels": {
                "awsclone": "true",
                "instance_id": instance_id,
                "owner": user.id,
            },
        }

        if port_bindings:
            run_kwargs["ports"] = port_bindings

        if network_name:
            run_kwargs["network"] = network_name

        if body.command:
            run_kwargs["command"] = body.command

        container = get_docker().containers.run(**run_kwargs)
    except docker.errors.ImageNotFound:
        raise HTTPException(status_code=400, detail=f"Image not found: {body.image}. It will be pulled on first use — try again in a moment.")
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    # Get private IP
    private_ip = None
    try:
        container.reload()
        nets = container.attrs["NetworkSettings"]["Networks"]
        for net_info in nets.values():
            if net_info.get("IPAddress"):
                private_ip = net_info["IPAddress"]
                break
    except Exception:
        pass

    # Save to DB
    instance = Instance(
        id=instance_id,
        name=body.name,
        owner_id=user.id,
        docker_container_id=container.id,
        image=body.image,
        instance_type=body.instance_type,
        state="running",
        vpc_id=body.vpc_id,
        private_ip=private_ip,
        port_mappings=json.dumps(resolved_ports) if resolved_ports else None,
        cpu_limit=itype["cpu"],
        memory_limit=itype["memory"],
        environment=json.dumps(body.environment or {}),
        command=body.command,
    )
    db.add(instance)
    await db.flush()

    return _instance_to_dict(instance)


@router.post("/instances/{instance_id}/stop")
async def stop_instance(
    instance_id: str,
    user: User = Depends(require_permission("ec2:StopInstance")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if instance.state != "running":
        raise HTTPException(status_code=400, detail="Instance is not running")

    instance.state = "stopping"
    await db.flush()

    try:
        container = get_docker().containers.get(instance.docker_container_id)
        container.stop(timeout=10)
    except docker.errors.NotFound:
        pass
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    instance.state = "stopped"
    await db.flush()
    return _instance_to_dict(instance)


@router.post("/instances/{instance_id}/start")
async def start_instance(
    instance_id: str,
    user: User = Depends(require_permission("ec2:StartInstance")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if instance.state != "stopped":
        raise HTTPException(status_code=400, detail="Instance is not stopped")

    try:
        container = get_docker().containers.get(instance.docker_container_id)
        container.start()
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container no longer exists")
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    instance.state = "running"

    # Refresh private IP
    try:
        container.reload()
        nets = container.attrs["NetworkSettings"]["Networks"]
        for net_info in nets.values():
            if net_info.get("IPAddress"):
                instance.private_ip = net_info["IPAddress"]
                break
    except Exception:
        pass

    await db.flush()
    return _instance_to_dict(instance)


@router.post("/instances/{instance_id}/reboot")
async def reboot_instance(
    instance_id: str,
    user: User = Depends(require_permission("ec2:RebootInstance")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if instance.state != "running":
        raise HTTPException(status_code=400, detail="Instance is not running")

    try:
        container = get_docker().containers.get(instance.docker_container_id)
        container.restart(timeout=10)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container no longer exists")
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    return _instance_to_dict(instance)


@router.delete("/instances/{instance_id}")
async def terminate_instance(
    instance_id: str,
    user: User = Depends(require_permission("ec2:TerminateInstance")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)

    # Remove Docker container
    if instance.docker_container_id:
        try:
            container = get_docker().containers.get(instance.docker_container_id)
            container.remove(force=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    instance.state = "terminated"
    instance.docker_container_id = None
    await db.flush()
    return {"detail": "Instance terminated"}


@router.get("/instances/{instance_id}/logs")
async def get_instance_logs(
    instance_id: str,
    tail: int = 200,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if not instance.docker_container_id:
        return {"logs": ""}

    try:
        container = get_docker().containers.get(instance.docker_container_id)
        logs = container.logs(stdout=True, stderr=True, tail=tail).decode("utf-8", errors="replace")
        return {"logs": logs}
    except docker.errors.NotFound:
        return {"logs": "Container not found"}
    except docker.errors.APIError:
        return {"logs": "Failed to fetch logs"}


@router.get("/instances/{instance_id}/stats")
async def get_instance_stats(
    instance_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if not instance.docker_container_id:
        raise HTTPException(status_code=400, detail="No container associated")

    try:
        container = get_docker().containers.get(instance.docker_container_id)
        stats = container.stats(stream=False)

        # Calculate CPU %
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        cpu_percent = (cpu_delta / system_delta) * 100.0 if system_delta > 0 else 0.0

        # Memory
        mem_usage = stats["memory_stats"].get("usage", 0) / (1024 * 1024)
        mem_limit = stats["memory_stats"].get("limit", 0) / (1024 * 1024)

        return {
            "cpu_percent": round(cpu_percent, 2),
            "memory_usage_mb": round(mem_usage, 2),
            "memory_limit_mb": round(mem_limit, 2),
            "memory_percent": round((mem_usage / mem_limit) * 100, 2) if mem_limit > 0 else 0,
        }
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    except docker.errors.APIError:
        raise HTTPException(status_code=500, detail="Failed to get stats")


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_instance(instance_id: str, db: AsyncSession) -> Instance:
    result = await db.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


def _instance_to_dict(instance: Instance) -> dict:
    port_mappings = None
    if instance.port_mappings:
        try:
            port_mappings = json.loads(instance.port_mappings)
        except json.JSONDecodeError:
            port_mappings = {}

    return {
        "id": instance.id,
        "name": instance.name,
        "owner_id": instance.owner_id,
        "docker_container_id": instance.docker_container_id,
        "image": instance.image,
        "instance_type": instance.instance_type,
        "state": instance.state,
        "vpc_id": instance.vpc_id,
        "private_ip": instance.private_ip,
        "port_mappings": port_mappings,
        "cpu_limit": instance.cpu_limit,
        "memory_limit": instance.memory_limit,
        "created_at": instance.created_at.isoformat(),
        "updated_at": instance.updated_at.isoformat(),
    }
