import asyncio
import io
import json
import re
import shutil
import subprocess
import tarfile
import threading
from typing import Optional

import docker
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission
from config import EC2_PORT_RANGE_START, EC2_PORT_RANGE_END, SERVER_PUBLIC_IP
from database import Instance, VPC, User, get_db, generate_id

# In-memory tunnel state: instance_id → {process, url}
_tunnels: dict[str, dict] = {}

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
    port_mappings: Optional[dict] = None
    environment: Optional[dict] = None
    command: Optional[str] = None


# ── Port allocation ──────────────────────────────────────────────────────

def _allocate_port() -> int:
    global _next_port
    with _port_lock:
        port = _next_port
        _next_port += 1
        if _next_port > EC2_PORT_RANGE_END:
            _next_port = EC2_PORT_RANGE_START
        return port


def _build_port_bindings(port_mappings: dict) -> tuple[dict, dict]:
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
    stmt = select(Instance).where(Instance.state != "terminated")
    if not user.is_root:
        stmt = stmt.where(Instance.owner_id == user.id)
    result = await db.execute(stmt)
    return [_instance_to_dict(i) for i in result.scalars().all()]


@router.get("/instances/{instance_id}")
async def get_instance(
    instance_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    return _instance_to_dict(await _get_instance(instance_id, db))


@router.post("/instances")
async def launch_instance(
    body: LaunchInstanceRequest,
    user: User = Depends(require_permission("ec2:RunInstance")),
    db: AsyncSession = Depends(get_db),
):
    itype = INSTANCE_TYPES.get(body.instance_type)
    if not itype:
        raise HTTPException(status_code=400, detail=f"Invalid instance type: {body.instance_type}")

    instance_id = generate_id()

    port_bindings = {}
    resolved_ports = {}
    if body.port_mappings:
        port_bindings, resolved_ports = _build_port_bindings(body.port_mappings)

    network_name = None
    if body.vpc_id:
        result = await db.execute(select(VPC).where(VPC.id == body.vpc_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="VPC not found")
        network_name = f"awsclone-vpc-{body.vpc_id}"

    # Run Docker operations off the event loop
    def _launch():
        run_kwargs = {
            "image": body.image,
            "name": f"awsclone-{instance_id}",
            "detach": True,
            "nano_cpus": itype["nano_cpus"],
            "mem_limit": itype["mem_limit"],
            "environment": body.environment or {},
            "labels": {"awsclone": "true", "instance_id": instance_id, "owner": user.id},
        }
        if port_bindings:
            run_kwargs["ports"] = port_bindings
        if network_name:
            run_kwargs["network"] = network_name
        if body.command:
            run_kwargs["command"] = body.command

        container = get_docker().containers.run(**run_kwargs)
        container.reload()
        return container

    try:
        container = await asyncio.to_thread(_launch)
    except docker.errors.ImageNotFound:
        raise HTTPException(status_code=400, detail=f"Image not found: {body.image}. Try again in a moment — it will be pulled.")
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    # Extract private IP
    private_ip = None
    try:
        for net_info in container.attrs["NetworkSettings"]["Networks"].values():
            if net_info.get("IPAddress"):
                private_ip = net_info["IPAddress"]
                break
    except Exception:
        pass

    instance = Instance(
        id=instance_id, name=body.name, owner_id=user.id,
        docker_container_id=container.id, image=body.image,
        instance_type=body.instance_type, state="running",
        vpc_id=body.vpc_id, private_ip=private_ip,
        port_mappings=json.dumps(resolved_ports) if resolved_ports else None,
        cpu_limit=itype["cpu"], memory_limit=itype["memory"],
        environment=json.dumps(body.environment or {}), command=body.command,
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
        await asyncio.to_thread(
            lambda: get_docker().containers.get(instance.docker_container_id).stop(timeout=10)
        )
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

    def _start():
        c = get_docker().containers.get(instance.docker_container_id)
        c.start()
        c.reload()
        return c

    try:
        container = await asyncio.to_thread(_start)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container no longer exists")
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    instance.state = "running"
    try:
        for net_info in container.attrs["NetworkSettings"]["Networks"].values():
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
        await asyncio.to_thread(
            lambda: get_docker().containers.get(instance.docker_container_id).restart(timeout=10)
        )
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

    if instance.docker_container_id:
        try:
            await asyncio.to_thread(
                lambda: get_docker().containers.get(instance.docker_container_id).remove(force=True)
            )
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
        logs = await asyncio.to_thread(
            lambda: get_docker().containers.get(instance.docker_container_id)
                .logs(stdout=True, stderr=True, tail=tail)
                .decode("utf-8", errors="replace")
        )
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
        stats = await asyncio.to_thread(
            lambda: get_docker().containers.get(instance.docker_container_id).stats(stream=False)
        )

        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
        cpu_pct = (cpu_delta / sys_delta) * 100.0 if sys_delta > 0 else 0.0
        mem_used = stats["memory_stats"].get("usage", 0) / (1024 * 1024)
        mem_limit = stats["memory_stats"].get("limit", 0) / (1024 * 1024)

        return {
            "cpu_percent": round(cpu_pct, 2),
            "memory_usage_mb": round(mem_used, 2),
            "memory_limit_mb": round(mem_limit, 2),
            "memory_percent": round((mem_used / mem_limit) * 100, 2) if mem_limit > 0 else 0,
        }
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    except docker.errors.APIError:
        raise HTTPException(status_code=500, detail="Failed to get stats")


@router.post("/instances/{instance_id}/tunnel/start")
async def start_tunnel(
    instance_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if instance.state != "running":
        raise HTTPException(status_code=400, detail="Instance must be running")

    # Stop any existing tunnel for this instance
    if instance_id in _tunnels:
        try:
            _tunnels[instance_id]["process"].kill()
        except Exception:
            pass
        del _tunnels[instance_id]

    # Find the host port for port 80 from port_mappings
    port_mappings = {}
    if instance.port_mappings:
        try:
            port_mappings = json.loads(instance.port_mappings)
        except json.JSONDecodeError:
            pass

    host_port = port_mappings.get("80") or port_mappings.get(80)
    if not host_port:
        raise HTTPException(status_code=400, detail="Instance has no port 80 mapping. Launch with port 80 exposed.")

    if not shutil.which("cloudflared"):
        raise HTTPException(status_code=500, detail="cloudflared is not installed on this server. Run: sudo dpkg -i cloudflared.deb")

    def _start_and_wait():
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://host.docker.internal:{host_port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        url = None
        # cloudflared prints the URL within the first ~30 lines of output
        for _ in range(60):
            line = proc.stdout.readline()
            if not line:
                break
            m = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                break
        return proc, url

    try:
        proc, url = await asyncio.to_thread(_start_and_wait)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start tunnel: {e}")

    if not url:
        try:
            proc.kill()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="cloudflared started but could not find tunnel URL. Check that cloudflared is working.")

    _tunnels[instance_id] = {"process": proc, "url": url}
    return {"tunnel_url": url}


@router.delete("/instances/{instance_id}/tunnel/stop")
async def stop_tunnel(
    instance_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    await _get_instance(instance_id, db)
    if instance_id not in _tunnels:
        raise HTTPException(status_code=404, detail="No active tunnel for this instance")
    try:
        _tunnels[instance_id]["process"].kill()
    except Exception:
        pass
    del _tunnels[instance_id]
    return {"detail": "Tunnel stopped"}


@router.post("/instances/{instance_id}/upload")
async def upload_files(
    instance_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if instance.state != "running":
        raise HTTPException(status_code=400, detail="Instance must be running to upload files")
    if not instance.docker_container_id:
        raise HTTPException(status_code=400, detail="No container associated")

    # Read all files first (async), then do Docker copy in thread
    file_data = []
    for upload in files:
        content = await upload.read()
        file_data.append((upload.filename, content))

    def _copy_files():
        container = get_docker().containers.get(instance.docker_container_id)
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for name, content in file_data:
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        tar_buffer.seek(0)
        container.put_archive("/usr/share/nginx/html", tar_buffer)

    try:
        await asyncio.to_thread(_copy_files)
        return {"detail": f"Uploaded {len(files)} file(s) successfully"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")


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

    public_urls = {}
    if port_mappings and SERVER_PUBLIC_IP:
        for container_port, host_port in port_mappings.items():
            public_urls[container_port] = f"http://{SERVER_PUBLIC_IP}:{host_port}"

    tunnel = _tunnels.get(instance.id)

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
        "public_urls": public_urls,
        "tunnel_url": tunnel["url"] if tunnel else None,
        "cpu_limit": instance.cpu_limit,
        "memory_limit": instance.memory_limit,
        "created_at": instance.created_at.isoformat(),
        "updated_at": instance.updated_at.isoformat(),
    }
