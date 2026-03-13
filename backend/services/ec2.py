import asyncio
import io
import json
import re
import shutil
import tarfile
import threading
from pathlib import Path
from typing import Optional

import docker
import httpx
import secrets as _secrets
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission, get_current_user
from config import EC2_PORT_RANGE_START, EC2_PORT_RANGE_END, SERVER_PUBLIC_IP, CADDY_ADMIN_URL
from database import Instance, VPC, User, get_db, generate_id, async_session

# Reuse project detection and Dockerfile generation from deploy service
from services.deploy import _detect_project, _generate_dockerfile, _extract_zip

# In-memory tunnel state: instance_id → {container_id, url}
_tunnels: dict[str, dict] = {}

router = APIRouter(prefix="/api/v1/ec2", tags=["ec2"])

# Project source files stored here
INSTANCE_PROJECTS_DIR = Path("/app/data/instance_projects")
INSTANCE_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Lazy Docker client
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

    # Clean up built image
    if instance.docker_image_tag:
        try:
            await asyncio.to_thread(
                lambda: get_docker().images.remove(instance.docker_image_tag, force=True)
            )
        except Exception:
            pass

    # Clean up project files
    project_dir = INSTANCE_PROJECTS_DIR / instance.id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    # Stop tunnel
    _stop_tunnel_container(instance.id)

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
        return {"logs": "", "build_log": instance.build_log or ""}

    try:
        logs = await asyncio.to_thread(
            lambda: get_docker().containers.get(instance.docker_container_id)
                .logs(stdout=True, stderr=True, tail=tail)
                .decode("utf-8", errors="replace")
        )
        return {"logs": logs, "build_log": instance.build_log or ""}
    except docker.errors.NotFound:
        return {"logs": "Container not found", "build_log": instance.build_log or ""}
    except docker.errors.APIError:
        return {"logs": "Failed to fetch logs", "build_log": instance.build_log or ""}


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


# ── Deploy into instance ─────────────────────────────────────────────────

_build_semaphore = asyncio.Semaphore(1)


async def _build_and_replace(instance_id: str, project_dir: Path, info: dict):
    """Build Docker image from source and replace the instance's container."""
    log_lines = []

    def log(msg: str):
        log_lines.append(msg)

    async def _save(state: str = None):
        async with async_session() as db:
            result = await db.execute(select(Instance).where(Instance.id == instance_id))
            inst = result.scalar_one_or_none()
            if inst:
                inst.build_log = "\n".join(log_lines)
                inst.project_type = info["type"]
                inst.project_label = info["label"]
                if state:
                    inst.state = state
                await db.commit()

    async with _build_semaphore:
        try:
            await _save("building")
            log(f"[detect] Project type: {info['label']} ({info['type']})")

            # Generate Dockerfile if needed
            dockerfile_content = _generate_dockerfile(info, project_dir)
            if dockerfile_content is not None:
                (project_dir / "Dockerfile").write_text(dockerfile_content)
                log("[build] Generated Dockerfile")
            else:
                log("[build] Using existing Dockerfile")

            # Determine container port
            is_static = info["type"] in ("static", "vite", "cra", "vue", "angular", "svelte", "node-static")
            container_port = 80 if is_static else info.get("port", 3000)

            image_tag = f"awsclone-instance-{instance_id}"
            log(f"[build] Building image: {image_tag} ...")

            def _docker_build():
                client = get_docker()
                image, build_logs = client.images.build(
                    path=str(project_dir),
                    tag=image_tag,
                    rm=True,
                    forcerm=True,
                )
                return image, build_logs

            try:
                image, build_logs = await asyncio.to_thread(_docker_build)
                for chunk in build_logs:
                    if "stream" in chunk:
                        line = chunk["stream"].strip()
                        if line:
                            log(f"  {line}")
                    if "error" in chunk:
                        log(f"  ERROR: {chunk['error']}")
                        raise Exception(chunk["error"])
            except docker.errors.BuildError as e:
                log(f"[build] FAILED: {e}")
                for item in e.build_log:
                    if "stream" in item:
                        log(f"  {item['stream'].strip()}")
                    if "error" in item:
                        log(f"  ERROR: {item['error']}")
                await _save("failed")
                return
            except Exception as e:
                log(f"[build] FAILED: {e}")
                await _save("failed")
                return

            log("[build] Image built successfully")

            # Get the instance's current port mappings to reuse the same host port
            async with async_session() as db:
                result = await db.execute(select(Instance).where(Instance.id == instance_id))
                inst = result.scalar_one_or_none()
                if not inst:
                    return

                port_mappings = {}
                if inst.port_mappings:
                    try:
                        port_mappings = json.loads(inst.port_mappings)
                    except json.JSONDecodeError:
                        pass

                # Use existing host port for port 80, or allocate one
                host_port = port_mappings.get("80") or port_mappings.get(80) or _allocate_port()
                old_container_id = inst.docker_container_id

            container_name = f"awsclone-{instance_id}"
            log(f"[deploy] Starting container on port {host_port} ...")

            def _replace_container():
                client = get_docker()
                # Stop and remove old container
                if old_container_id:
                    try:
                        old = client.containers.get(old_container_id)
                        old.remove(force=True)
                    except docker.errors.NotFound:
                        pass
                # Also try by name
                try:
                    old = client.containers.get(container_name)
                    old.remove(force=True)
                except docker.errors.NotFound:
                    pass

                container = client.containers.run(
                    image_tag,
                    detach=True,
                    name=container_name,
                    ports={f"{container_port}/tcp": ("0.0.0.0", host_port)},
                    labels={"awsclone": "true", "instance_id": instance_id},
                    mem_limit="512m",
                    restart_policy={"Name": "unless-stopped"},
                    network="awsclone-internal",
                )
                container.reload()
                return container

            try:
                container = await asyncio.to_thread(_replace_container)
            except Exception as e:
                log(f"[deploy] FAILED to start container: {e}")
                await _save("failed")
                return

            log(f"[deploy] Container started: {container.short_id}")

            # Verify container is still running after startup (catches immediate crashes)
            await asyncio.sleep(2)

            def _check_running():
                container.reload()
                return container.status, container.logs(tail=50).decode("utf-8", errors="replace")

            try:
                container_status, startup_logs = await asyncio.to_thread(_check_running)
                if startup_logs.strip():
                    log(f"[deploy] Container startup logs:\n{startup_logs.rstrip()}")
                if container_status != "running":
                    log(f"[deploy] Container exited (status={container_status}). App may be crashing or binding to wrong host/port.")
                    await _save("failed")
                    return
                log(f"[deploy] Container verified running (docker ps status={container_status})")
            except Exception as e:
                log(f"[deploy] Warning: could not verify container status: {e}")

            # Add Caddy reverse proxy route so the app is accessible via port 80
            try:
                await _add_caddy_route_ec2(instance_id, container_name, container_port)
                log(f"[deploy] Caddy route added: /instance/{instance_id}/")
            except Exception as e:
                log(f"[deploy] Warning: Could not add Caddy route: {e}")

            deploy_url = f"http://{SERVER_PUBLIC_IP or 'localhost'}/instance/{instance_id}/"
            log(f"[deploy] URL: {deploy_url}")
            log("[deploy] Deployment successful!")

            # Extract private IP
            private_ip = None
            try:
                for net_info in container.attrs["NetworkSettings"]["Networks"].values():
                    if net_info.get("IPAddress"):
                        private_ip = net_info["IPAddress"]
                        break
            except Exception:
                pass

            # Save final state
            new_port_mappings = {str(container_port): host_port}
            async with async_session() as db:
                result = await db.execute(select(Instance).where(Instance.id == instance_id))
                inst = result.scalar_one_or_none()
                if inst:
                    inst.state = "running"
                    inst.build_log = "\n".join(log_lines)
                    inst.docker_container_id = container.id
                    inst.docker_image_tag = image_tag
                    inst.image = image_tag
                    inst.project_type = info["type"]
                    inst.project_label = info["label"]
                    inst.port_mappings = json.dumps(new_port_mappings)
                    inst.private_ip = private_ip
                    await db.commit()

        except Exception as e:
            log(f"[error] Unexpected: {e}")
            await _save("failed")


@router.post("/instances/{instance_id}/deploy/github")
async def deploy_github_to_instance(
    instance_id: str,
    github_repo: str = Form(...),
    github_branch: str = Form("main"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deploy a GitHub repo into this instance (replaces container with built image)."""
    instance = await _get_instance(instance_id, db)
    if not user.github_token:
        raise HTTPException(status_code=400, detail="GitHub not connected")

    webhook_secret = _secrets.token_hex(32)

    # Update instance with GitHub info
    instance.github_repo = github_repo
    instance.github_branch = github_branch
    instance.webhook_secret = webhook_secret
    instance.state = "building"
    instance.build_log = "Starting GitHub deploy..."
    await db.flush()

    project_dir = INSTANCE_PROJECTS_DIR / instance_id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    # Download repo ZIP
    try:
        zip_bytes = await _download_github_zip(user.github_token, github_repo, github_branch)
    except Exception as e:
        instance.state = "failed"
        instance.build_log = f"Failed to download repository: {e}"
        await db.flush()
        raise HTTPException(status_code=500, detail=f"Failed to download repo: {e}")

    _extract_zip(zip_bytes, project_dir)

    # Register webhook
    webhook_id = await _register_github_webhook(user.github_token, github_repo, webhook_secret)
    if webhook_id:
        instance.github_webhook_id = webhook_id
    await db.flush()

    # Detect and build in background
    info = _detect_project(project_dir)
    asyncio.create_task(_build_and_replace(instance_id, project_dir, info))
    return _instance_to_dict(instance)


@router.post("/instances/{instance_id}/deploy/zip")
async def deploy_zip_to_instance(
    instance_id: str,
    file: UploadFile = File(...),
    user: User = Depends(require_permission("ec2:RunInstance")),
    db: AsyncSession = Depends(get_db),
):
    """Deploy a ZIP file into this instance (replaces container with built image)."""
    instance = await _get_instance(instance_id, db)

    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a ZIP file")

    instance.state = "building"
    instance.build_log = "Extracting ZIP..."
    instance.github_repo = None
    instance.github_branch = None
    await db.flush()

    project_dir = INSTANCE_PROJECTS_DIR / instance_id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    zip_bytes = await file.read()
    try:
        await asyncio.to_thread(_extract_zip, zip_bytes, project_dir)
    except Exception as e:
        instance.state = "failed"
        instance.build_log = f"Failed to extract ZIP: {e}"
        await db.flush()
        raise HTTPException(status_code=400, detail=f"Invalid ZIP file: {e}")

    info = _detect_project(project_dir)
    if info["type"] == "unknown":
        instance.state = "failed"
        instance.build_log = "Could not detect project type. Include index.html, package.json, requirements.txt, or a Dockerfile."
        await db.flush()
        raise HTTPException(status_code=400, detail="Unknown project type.")

    asyncio.create_task(_build_and_replace(instance_id, project_dir, info))
    return _instance_to_dict(instance)


@router.get("/instances/{instance_id}/files")
async def list_instance_files(
    instance_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    """List project source files for this instance."""
    await _get_instance(instance_id, db)
    project_dir = INSTANCE_PROJECTS_DIR / instance_id
    if not project_dir.exists():
        return []

    skip = {'node_modules', '.git', '__pycache__', '.next', 'dist', 'build', '.cache', 'venv', '.venv'}
    files = []
    for path in sorted(project_dir.rglob("*")):
        if any(part in skip for part in path.relative_to(project_dir).parts):
            continue
        if path.is_file():
            rel = str(path.relative_to(project_dir))
            files.append({"path": rel, "size": path.stat().st_size})
            if len(files) >= 500:
                break
    return files


async def instance_github_redeploy(instance_id: str):
    """Re-download from GitHub and rebuild. Called by webhook handler."""
    async with async_session() as db:
        result = await db.execute(select(Instance).where(Instance.id == instance_id))
        inst = result.scalar_one_or_none()
        if not inst or not inst.github_repo:
            return

        result2 = await db.execute(select(User).where(User.id == inst.owner_id))
        owner = result2.scalar_one_or_none()
        if not owner or not owner.github_token:
            return

        inst.state = "building"
        inst.build_log = "Auto-redeploy triggered by GitHub push...\n"
        await db.commit()

    project_dir = INSTANCE_PROJECTS_DIR / instance_id
    shutil.rmtree(project_dir, ignore_errors=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        zip_bytes = await _download_github_zip(owner.github_token, inst.github_repo, inst.github_branch or "main")
    except Exception as e:
        async with async_session() as db:
            result = await db.execute(select(Instance).where(Instance.id == instance_id))
            inst2 = result.scalar_one_or_none()
            if inst2:
                inst2.state = "failed"
                inst2.build_log = f"Failed to download for redeploy: {e}"
                await db.commit()
        return

    _extract_zip(zip_bytes, project_dir)
    info = _detect_project(project_dir)
    await _build_and_replace(instance_id, project_dir, info)


# ── Tunnel ───────────────────────────────────────────────────────────────

@router.post("/instances/{instance_id}/tunnel/start")
async def start_tunnel(
    instance_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db)
    if instance.state != "running":
        raise HTTPException(status_code=400, detail="Instance must be running")

    # Verify the instance container is actually running before starting tunnel
    if instance.docker_container_id:
        def _verify_container():
            try:
                c = get_docker().containers.get(instance.docker_container_id)
                c.reload()
                return c.status
            except docker.errors.NotFound:
                return "not_found"
        container_status = await asyncio.to_thread(_verify_container)
        if container_status != "running":
            raise HTTPException(
                status_code=400,
                detail=f"Instance container is not running (status={container_status}). Check logs for errors."
            )

    _stop_tunnel_container(instance_id)

    port_mappings = {}
    if instance.port_mappings:
        try:
            port_mappings = json.loads(instance.port_mappings)
        except json.JSONDecodeError:
            pass

    host_port = port_mappings.get("80") or port_mappings.get(80)
    if not host_port:
        # Try first available port
        if port_mappings:
            host_port = list(port_mappings.values())[0]
        else:
            raise HTTPException(status_code=400, detail="Instance has no port mappings.")

    def _start_and_wait():
        import time
        container_name = f"awsclone-tunnel-{instance_id}"
        container = get_docker().containers.run(
            "cloudflare/cloudflared:latest",
            command=["tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{host_port}"],
            network_mode="host",
            detach=True,
            name=container_name,
            labels={"awsclone": "true", "tunnel_for": instance_id},
            remove=False,
        )
        url = None
        for _ in range(30):
            time.sleep(1)
            logs = container.logs().decode("utf-8", errors="replace")
            m = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", logs)
            if m:
                url = m.group(0)
                break
        return container, url

    try:
        container, url = await asyncio.to_thread(_start_and_wait)
    except docker.errors.ImageNotFound:
        raise HTTPException(status_code=500, detail="Pulling cloudflared image... Try again in 30 seconds.")
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    if not url:
        try:
            container.remove(force=True)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Tunnel started but could not get URL.")

    _tunnels[instance_id] = {"container_id": container.id, "url": url}
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
    _stop_tunnel_container(instance_id)
    return {"detail": "Tunnel stopped"}


def _stop_tunnel_container(instance_id: str):
    if instance_id in _tunnels:
        try:
            c = get_docker().containers.get(_tunnels[instance_id]["container_id"])
            c.remove(force=True)
        except Exception:
            pass
        del _tunnels[instance_id]
    try:
        c = get_docker().containers.get(f"awsclone-tunnel-{instance_id}")
        c.remove(force=True)
    except Exception:
        pass


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


# ── GitHub helpers ────────────────────────────────────────────────────────

async def _download_github_zip(token: str, repo: str, branch: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/zipball/{branch}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=120,
        )
        if resp.status_code == 404:
            raise Exception(f"Repository or branch not found: {repo}@{branch}")
        if resp.status_code != 200:
            raise Exception(f"GitHub API error {resp.status_code}")
        return resp.content


async def _register_github_webhook(token: str, repo: str, secret: str) -> int | None:
    from config import PUBLIC_BASE_URL
    webhook_url = f"{PUBLIC_BASE_URL}/api/v1/github/webhook"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{repo}/hooks",
                json={
                    "name": "web",
                    "active": True,
                    "events": ["push"],
                    "config": {
                        "url": webhook_url,
                        "content_type": "json",
                        "secret": secret,
                        "insecure_ssl": "0",
                    },
                },
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                timeout=15,
            )
            if resp.status_code == 201:
                return resp.json().get("id")
    except Exception:
        pass
    return None


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_instance(instance_id: str, db: AsyncSession) -> Instance:
    result = await db.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


async def _add_caddy_route_ec2(instance_id: str, container_name: str, container_port: int):
    """Add a reverse proxy route in Caddy for an EC2 instance."""
    route_id = f"instance-{instance_id}"
    route_config = {
        "@id": route_id,
        "match": [{"path": [f"/instance/{instance_id}/*"]}],
        "handle": [
            {
                "handler": "rewrite",
                "strip_path_prefix": f"/instance/{instance_id}",
            },
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": f"{container_name}:{container_port}"}],
            },
        ],
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{CADDY_ADMIN_URL}/config/apps/http/servers/srv0/routes",
            json=route_config,
            timeout=10,
        )


async def _remove_caddy_route_ec2(instance_id: str):
    """Remove a Caddy route for an EC2 instance."""
    route_id = f"instance-{instance_id}"
    async with httpx.AsyncClient() as client:
        try:
            await client.delete(f"{CADDY_ADMIN_URL}/id/{route_id}", timeout=10)
        except Exception:
            pass


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

    # If this instance was built from source (has project_type), provide Caddy-proxied URL
    instance_url = None
    if instance.project_type and SERVER_PUBLIC_IP:
        instance_url = f"http://{SERVER_PUBLIC_IP}/instance/{instance.id}/"

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
        "instance_url": instance_url,
        "tunnel_url": tunnel["url"] if tunnel else None,
        "cpu_limit": instance.cpu_limit,
        "memory_limit": instance.memory_limit,
        # Deploy fields
        "github_repo": instance.github_repo,
        "github_branch": instance.github_branch,
        "github_webhook_id": instance.github_webhook_id,
        "project_type": instance.project_type,
        "project_label": instance.project_label,
        "build_log": instance.build_log,
        "created_at": instance.created_at.isoformat(),
        "updated_at": instance.updated_at.isoformat(),
    }
