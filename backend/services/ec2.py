import asyncio
import io
import json
import os
import re
import secrets as _secrets
import shutil
import tarfile
import threading
from pathlib import Path
from typing import Optional

import docker
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission, get_current_user
from config import EC2_PORT_RANGE_START, EC2_PORT_RANGE_END, SERVER_PUBLIC_IP, BASE_DOMAIN
from database import Instance, VPC, User, get_db, generate_id, async_session
from services.deploy import _detect_project, _generate_dockerfile, _extract_zip
from services.traefik import write_subdomain_route, remove_subdomain_route

# Project source files stored here
INSTANCE_PROJECTS_DIR = Path("/app/data/instance_projects")
INSTANCE_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(prefix="/api/v1/ec2", tags=["ec2"])

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
    return _instance_to_dict(await _get_instance(instance_id, db, user))


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
        vpc = result.scalar_one_or_none()
        if not vpc:
            raise HTTPException(status_code=404, detail="VPC not found")
        if not user.is_root and vpc.owner_id != user.id:
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
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
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
    instance = await _get_instance(instance_id, db, user)
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
    instance = await _get_instance(instance_id, db, user)
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
    instance = await _get_instance(instance_id, db, user)
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
    instance = await _get_instance(instance_id, db, user)

    if instance.docker_container_id:
        try:
            await asyncio.to_thread(
                lambda: get_docker().containers.get(instance.docker_container_id).remove(force=True)
            )
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            raise HTTPException(status_code=500, detail=f"Docker error: {e.explanation}")

    # Clean up built image and project files
    if instance.docker_image_tag:
        try:
            await asyncio.to_thread(
                lambda: get_docker().images.remove(instance.docker_image_tag, force=True)
            )
        except Exception:
            pass
    project_dir = INSTANCE_PROJECTS_DIR / instance.id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    # Remove Traefik subdomain route if one was set
    if instance.subdomain:
        remove_subdomain_route(instance.subdomain)

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
    instance = await _get_instance(instance_id, db, user)
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
    instance = await _get_instance(instance_id, db, user)
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


# ── GitHub deploy into instance ──────────────────────────────────────────

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

            dockerfile_content = _generate_dockerfile(info, project_dir)
            if dockerfile_content is not None:
                (project_dir / "Dockerfile").write_text(dockerfile_content)
                log("[build] Generated Dockerfile")
            else:
                log("[build] Using existing Dockerfile")

            is_static = info["type"] in ("static", "vite", "cra", "vue", "angular", "svelte", "node-static")
            container_port = 80 if is_static else info.get("port", 3000)

            image_tag = f"awsclone-instance-{instance_id}"
            log(f"[build] Building image: {image_tag} ...")

            def _docker_build():
                client = get_docker()
                image, build_logs = client.images.build(
                    path=str(project_dir), tag=image_tag, rm=True, forcerm=True,
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
                host_port = port_mappings.get(str(container_port)) or port_mappings.get(container_port) or _allocate_port()
                old_container_id = inst.docker_container_id

            container_name = f"awsclone-{instance_id}"
            log(f"[deploy] Starting container on port {host_port} ...")

            def _replace_container():
                client = get_docker()
                if old_container_id:
                    try:
                        client.containers.get(old_container_id).remove(force=True)
                    except docker.errors.NotFound:
                        pass
                try:
                    client.containers.get(container_name).remove(force=True)
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
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
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

            # Verify container stays running (catches crash-loops)
            await asyncio.sleep(5)

            def _check_running():
                container.reload()
                return container.status, container.logs(tail=50).decode("utf-8", errors="replace")

            try:
                container_status, startup_logs = await asyncio.to_thread(_check_running)
                if startup_logs.strip():
                    log(f"[deploy] Container startup logs:\n{startup_logs.rstrip()}")
                if container_status == "restarting":
                    log("[deploy] Container is crash-looping. Common causes: missing env vars/database, app binds to 127.0.0.1 internally.")
                    await _save("failed")
                    return
                if container_status != "running":
                    log(f"[deploy] Container exited (status={container_status}). Check startup logs above.")
                    await _save("failed")
                    return
                log(f"[deploy] Container verified running (status={container_status})")
            except Exception as e:
                log(f"[deploy] Warning: could not verify container status: {e}")

            # Add Traefik subdomain route if subdomain is set
            async with async_session() as db:
                result = await db.execute(select(Instance).where(Instance.id == instance_id))
                inst_check = result.scalar_one_or_none()
                subdomain = inst_check.subdomain if inst_check else None

            if subdomain:
                try:
                    write_subdomain_route(subdomain, BASE_DOMAIN, container_name, container_port)
                    log(f"[deploy] Traefik route added: {subdomain}.{BASE_DOMAIN}")
                except Exception as e:
                    log(f"[deploy] Warning: Could not add Traefik route: {e}")
                log(f"[deploy] Website URL: https://{subdomain}.{BASE_DOMAIN}")
            else:
                log("[deploy] No subdomain set — set one in the Website tab to get a URL")
            log("[deploy] Deployment successful!")

            private_ip = None
            try:
                for net_info in container.attrs["NetworkSettings"]["Networks"].values():
                    if net_info.get("IPAddress"):
                        private_ip = net_info["IPAddress"]
                        break
            except Exception:
                pass

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
                    inst.port_mappings = json.dumps({str(container_port): host_port})
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
    """Deploy a GitHub repo as the website for this instance."""
    instance = await _get_instance(instance_id, db, user)
    if not user.github_token:
        raise HTTPException(status_code=400, detail="GitHub not connected")

    webhook_secret = _secrets.token_hex(32)
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

    try:
        zip_bytes = await _download_github_zip(user.github_token, github_repo, github_branch)
    except Exception as e:
        instance.state = "failed"
        instance.build_log = f"Failed to download repository: {e}"
        await db.flush()
        raise HTTPException(status_code=500, detail=f"Failed to download repo: {e}")

    _extract_zip(zip_bytes, project_dir)

    webhook_id = await _register_github_webhook(user.github_token, github_repo, webhook_secret)
    if webhook_id:
        instance.github_webhook_id = webhook_id
    await db.flush()

    info = _detect_project(project_dir)
    asyncio.create_task(_build_and_replace(instance_id, project_dir, info))
    return _instance_to_dict(instance)


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
                    "name": "web", "active": True, "events": ["push"],
                    "config": {"url": webhook_url, "content_type": "json", "secret": secret, "insecure_ssl": "0"},
                },
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                timeout=15,
            )
            if resp.status_code == 201:
                return resp.json().get("id")
    except Exception:
        pass
    return None




# ── Custom subdomain ─────────────────────────────────────────────────────

@router.post("/instances/{instance_id}/subdomain")
async def set_subdomain(
    instance_id: str,
    subdomain: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set a custom subdomain for this instance's website."""
    instance = await _get_instance(instance_id, db, user)

    # Validate subdomain
    subdomain = subdomain.strip().lower()
    if not re.match(r'^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$', subdomain):
        raise HTTPException(
            status_code=400,
            detail="Subdomain must be lowercase letters, numbers, and hyphens (2-63 chars, can't start/end with hyphen)",
        )

    # Check uniqueness
    existing = await db.execute(
        select(Instance).where(Instance.subdomain == subdomain, Instance.id != instance_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Subdomain '{subdomain}' is already taken")

    old_subdomain = instance.subdomain
    instance.subdomain = subdomain
    await db.flush()

    # Update Traefik route if the instance has a running website
    if instance.state == "running" and instance.port_mappings:
        try:
            port_mappings = json.loads(instance.port_mappings)
        except json.JSONDecodeError:
            port_mappings = {}
        container_name = f"awsclone-{instance_id}"
        # Determine container port
        is_static = instance.project_type in ("static", "vite", "cra", "vue", "angular", "svelte", "node-static")
        container_port = 80 if is_static else 3000
        if old_subdomain:
            remove_subdomain_route(old_subdomain)
        write_subdomain_route(subdomain, BASE_DOMAIN, container_name, container_port)

    return _instance_to_dict(instance)


@router.post("/instances/{instance_id}/upload")
async def upload_files(
    instance_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    instance = await _get_instance(instance_id, db, user)
    if instance.state != "running":
        raise HTTPException(status_code=400, detail="Instance must be running to upload files")
    if not instance.docker_container_id:
        raise HTTPException(status_code=400, detail="No container associated")

    file_data = []
    for upload in files:
        content = await upload.read()
        safe_name = os.path.basename(upload.filename or "upload")
        safe_name = re.sub(r'[^\w.\-]', '_', safe_name)
        if not safe_name or safe_name.startswith('.'):
            safe_name = "upload_" + safe_name
        file_data.append((safe_name, content))

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

async def _get_instance(instance_id: str, db: AsyncSession, user: User) -> Instance:
    result = await db.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    if not user.is_root and instance.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


def _instance_to_dict(instance: Instance) -> dict:
    port_mappings = None
    if instance.port_mappings:
        try:
            port_mappings = json.loads(instance.port_mappings)
        except json.JSONDecodeError:
            port_mappings = {}

    # Website URL via custom subdomain
    website_url = None
    if instance.subdomain and BASE_DOMAIN:
        website_url = f"https://{instance.subdomain}.{BASE_DOMAIN}"

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
        "subdomain": instance.subdomain,
        "website_url": website_url,
        "cpu_limit": instance.cpu_limit,
        "memory_limit": instance.memory_limit,
        "github_repo": instance.github_repo,
        "github_branch": instance.github_branch,
        "project_label": instance.project_label,
        "build_log": instance.build_log,
        "created_at": instance.created_at.isoformat(),
        "updated_at": instance.updated_at.isoformat(),
    }
