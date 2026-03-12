import asyncio
import io
import json
import os
import re
import shutil
import tarfile
import threading
import zipfile
from pathlib import Path
from typing import Optional

import docker
import httpx
import secrets as _secrets
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission, get_current_user
from config import SERVER_PUBLIC_IP, CADDY_ADMIN_URL
from database import Deployment, User, get_db, generate_id, async_session

router = APIRouter(prefix="/api/v1/deploy", tags=["deploy"])

DEPLOYS_DIR = Path("/app/data/deploys")
DEPLOYS_DIR.mkdir(parents=True, exist_ok=True)

# Port counter for deployments (separate range from EC2)
_port_lock = threading.Lock()
_next_deploy_port = 40000


def _allocate_port() -> int:
    global _next_deploy_port
    with _port_lock:
        port = _next_deploy_port
        _next_deploy_port += 1
        if _next_deploy_port > 41000:
            _next_deploy_port = 40000
        return port


def _get_docker():
    return docker.from_env()


# ── Project detection ──────────────────────────────────────────────────

def _detect_project(project_dir: Path) -> dict:
    """Analyze project files and return type info."""

    if (project_dir / "Dockerfile").exists():
        return {"type": "dockerfile", "label": "Custom Dockerfile"}

    pkg_file = project_dir / "package.json"
    if pkg_file.exists():
        try:
            pkg = json.loads(pkg_file.read_text())
        except json.JSONDecodeError:
            pkg = {}

        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        scripts = pkg.get("scripts", {})

        if "next" in deps:
            return {"type": "nextjs", "label": "Next.js", "port": 3000}

        if "build" in scripts:
            if "vite" in deps or (project_dir / "vite.config.js").exists() or (project_dir / "vite.config.ts").exists():
                return {"type": "vite", "label": "Vite", "output": "dist"}
            if "react-scripts" in deps:
                return {"type": "cra", "label": "React (CRA)", "output": "build"}
            if "@angular/core" in deps:
                return {"type": "angular", "label": "Angular", "output": "dist"}
            if "svelte" in deps or "@sveltejs/kit" in deps:
                return {"type": "svelte", "label": "Svelte", "output": "build"}
            if "@vue/cli-service" in deps or "vue" in deps:
                return {"type": "vue", "label": "Vue", "output": "dist"}
            return {"type": "node-static", "label": "Node.js (static)", "output": "dist"}

        if "start" in scripts or "main" in pkg:
            return {"type": "node-server", "label": "Node.js Server", "port": 3000}

    if (project_dir / "requirements.txt").exists():
        reqs = (project_dir / "requirements.txt").read_text().lower()
        if "fastapi" in reqs or "uvicorn" in reqs:
            return {"type": "python", "label": "Python (FastAPI)", "port": 8000}
        if "flask" in reqs:
            return {"type": "python", "label": "Python (Flask)", "port": 5000}
        if "django" in reqs:
            return {"type": "python", "label": "Python (Django)", "port": 8000}
        return {"type": "python", "label": "Python", "port": 8000}

    if any((project_dir / f).exists() for f in ["index.html", "index.htm"]):
        return {"type": "static", "label": "Static HTML"}

    return {"type": "unknown", "label": "Unknown"}


# ── Dockerfile generation ──────────────────────────────────────────────

def _generate_dockerfile(info: dict, project_dir: Path) -> Optional[str]:
    t = info["type"]

    if t == "dockerfile":
        return None  # use existing

    if t == "static":
        return "FROM nginx:alpine\nCOPY . /usr/share/nginx/html\nEXPOSE 80"

    if t in ("vite", "cra", "vue", "angular", "svelte", "node-static"):
        out = info.get("output", "dist")
        return f"""FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/{out} /usr/share/nginx/html
EXPOSE 80"""

    if t == "nextjs":
        return """FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]"""

    if t == "node-server":
        return """FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
EXPOSE 3000
CMD ["npm", "start"]"""

    if t == "python":
        entry = "app.py"
        for f in ["main.py", "app.py", "server.py", "run.py", "manage.py"]:
            if (project_dir / f).exists():
                entry = f
                break

        reqs = ""
        if (project_dir / "requirements.txt").exists():
            reqs = (project_dir / "requirements.txt").read_text().lower()

        port = info.get("port", 8000)
        if "fastapi" in reqs or "uvicorn" in reqs:
            module = entry[:-3]
            cmd = f'CMD ["uvicorn", "{module}:app", "--host", "0.0.0.0", "--port", "{port}"]'
        elif "django" in reqs:
            cmd = f'CMD ["python", "manage.py", "runserver", "0.0.0.0:{port}"]'
        else:
            cmd = f'CMD ["python", "{entry}"]'

        return f"""FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE {port}
{cmd}"""

    return None


# ── Background build worker ───────────────────────────────────────────

_build_semaphore = asyncio.Semaphore(1)  # 1 build at a time (4GB RAM)


async def _build_and_deploy(deploy_id: str, project_dir: Path, info: dict):
    """Build Docker image and start container. Runs in background."""
    log_lines = []

    def log(msg: str):
        log_lines.append(msg)

    async def _save_status(status: str):
        async with async_session() as db:
            result = await db.execute(select(Deployment).where(Deployment.id == deploy_id))
            dep = result.scalar_one_or_none()
            if dep:
                dep.status = status
                dep.build_log = "\n".join(log_lines)
                dep.project_type = info["type"]
                dep.project_label = info["label"]
                await db.commit()

    async with _build_semaphore:
        try:
            await _save_status("building")
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

            image_tag = f"awsclone-deploy-{deploy_id}"
            log(f"[build] Building image: {image_tag} ...")

            # Build image in thread (blocking Docker SDK call)
            def _docker_build():
                client = _get_docker()
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
                await _save_status("failed")
                return
            except Exception as e:
                log(f"[build] FAILED: {e}")
                await _save_status("failed")
                return

            log("[build] Image built successfully")

            # Allocate host port and run container
            host_port = _allocate_port()
            container_name = f"awsclone-deploy-{deploy_id}"

            log(f"[deploy] Starting container on port {host_port} ...")

            def _docker_run():
                client = _get_docker()
                # Remove old container if exists
                try:
                    old = client.containers.get(container_name)
                    old.remove(force=True)
                except docker.errors.NotFound:
                    pass

                container = client.containers.run(
                    image_tag,
                    detach=True,
                    name=container_name,
                    ports={f"{container_port}/tcp": host_port},
                    labels={"awsclone": "true", "deploy_id": deploy_id},
                    mem_limit="256m",
                    restart_policy={"Name": "unless-stopped"},
                    network="awsclone-internal",
                )
                container.reload()
                return container

            try:
                container = await asyncio.to_thread(_docker_run)
            except Exception as e:
                log(f"[deploy] FAILED to start container: {e}")
                await _save_status("failed")
                return

            log(f"[deploy] Container started: {container.short_id}")

            # Add Caddy reverse proxy route so the app is accessible via port 80
            try:
                await _add_caddy_route(deploy_id, container_name, container_port)
                log(f"[deploy] Caddy route added: /deploy/{deploy_id}/")
            except Exception as e:
                log(f"[deploy] Warning: Could not add Caddy route: {e}")

            deploy_url = f"http://{SERVER_PUBLIC_IP or 'localhost'}/deploy/{deploy_id}/"
            log(f"[deploy] URL: {deploy_url}")
            log("[deploy] Deployment successful!")

            # Save final state
            async with async_session() as db:
                result = await db.execute(select(Deployment).where(Deployment.id == deploy_id))
                dep = result.scalar_one_or_none()
                if dep:
                    dep.status = "running"
                    dep.build_log = "\n".join(log_lines)
                    dep.docker_image_id = image.id
                    dep.docker_container_id = container.id
                    dep.port = host_port
                    dep.project_type = info["type"]
                    dep.project_label = info["label"]
                    await db.commit()

        except Exception as e:
            log(f"[error] Unexpected: {e}")
            await _save_status("failed")


# ── Caddy route management ───────────────────────────────────────────

async def _add_caddy_route(deploy_id: str, container_name: str, container_port: int):
    """Add a reverse proxy route in Caddy so the deploy is accessible via /deploy/{id}/."""
    route_id = f"deploy-{deploy_id}"
    route_config = {
        "@id": route_id,
        "match": [{"path": [f"/deploy/{deploy_id}/*"]}],
        "handle": [
            {
                "handler": "rewrite",
                "strip_path_prefix": f"/deploy/{deploy_id}",
            },
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": f"{container_name}:{container_port}"}],
            },
        ],
    }
    async with httpx.AsyncClient() as client:
        # Prepend route so it's matched before the catch-all file_server
        resp = await client.post(
            f"{CADDY_ADMIN_URL}/config/apps/http/servers/srv0/routes",
            json=route_config,
            timeout=10,
        )
        # If srv0 doesn't exist, try without server name (Caddyfile auto-names it)
        if resp.status_code >= 400:
            resp = await client.post(
                f"{CADDY_ADMIN_URL}/config/apps/http/servers",
                timeout=10,
            )


async def _remove_caddy_route(deploy_id: str):
    """Remove a Caddy route for a deployment."""
    route_id = f"deploy-{deploy_id}"
    async with httpx.AsyncClient() as client:
        try:
            await client.delete(
                f"{CADDY_ADMIN_URL}/id/{route_id}",
                timeout=10,
            )
        except Exception:
            pass


# ── Extract ZIP with folder normalization ────────────────────────────

def _extract_zip(zip_bytes: bytes, dest: Path):
    """Extract ZIP, flattening if there's a single top-level directory."""
    dest.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(buf) as zf:
        # Check if all entries share a common top-level dir
        names = zf.namelist()
        top_dirs = set()
        for n in names:
            parts = n.split("/")
            if len(parts) > 1:
                top_dirs.add(parts[0])
            else:
                top_dirs.add("")  # file at root

        # If exactly one top-level dir and no root files, flatten
        flatten = len(top_dirs) == 1 and "" not in top_dirs
        prefix = top_dirs.pop() + "/" if flatten else ""

        for member in zf.infolist():
            if member.is_dir():
                continue
            # Strip the prefix if flattening
            target = member.filename
            if flatten and target.startswith(prefix):
                target = target[len(prefix):]
            if not target:
                continue

            target_path = dest / target
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target_path, "wb") as dst:
                dst.write(src.read())


# ── API Endpoints ─────────────────────────────────────────────────────

@router.post("/projects")
async def create_deployment(
    name: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_permission("ec2:RunInstance")),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a ZIP file")

    deploy_id = generate_id()
    project_dir = DEPLOYS_DIR / deploy_id

    # Save deployment record
    deployment = Deployment(
        id=deploy_id,
        name=name,
        owner_id=user.id,
        status="uploading",
    )
    db.add(deployment)
    await db.flush()

    # Read and extract ZIP
    zip_bytes = await file.read()
    try:
        await asyncio.to_thread(_extract_zip, zip_bytes, project_dir)
    except Exception as e:
        deployment.status = "failed"
        deployment.build_log = f"Failed to extract ZIP: {e}"
        await db.flush()
        raise HTTPException(status_code=400, detail=f"Invalid ZIP file: {e}")

    # Detect project type
    info = _detect_project(project_dir)
    if info["type"] == "unknown":
        deployment.status = "failed"
        deployment.build_log = "Could not detect project type. Include index.html, package.json, requirements.txt, or a Dockerfile."
        deployment.project_type = "unknown"
        deployment.project_label = "Unknown"
        await db.flush()
        raise HTTPException(status_code=400, detail="Unknown project type. Make sure your ZIP contains a recognizable project.")

    deployment.project_type = info["type"]
    deployment.project_label = info["label"]
    deployment.status = "queued"
    await db.flush()

    # Start background build
    asyncio.create_task(_build_and_deploy(deploy_id, project_dir, info))

    return _deploy_to_dict(deployment)


@router.get("/projects")
async def list_deployments(
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Deployment).order_by(Deployment.created_at.desc())
    if not user.is_root:
        stmt = stmt.where(Deployment.owner_id == user.id)
    result = await db.execute(stmt)
    return [_deploy_to_dict(d) for d in result.scalars().all()]


@router.get("/projects/{deploy_id}")
async def get_deployment(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)
    return _deploy_to_dict(dep)


@router.get("/projects/{deploy_id}/logs")
async def get_deploy_logs(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)

    # Also get runtime logs if container is running
    runtime_logs = ""
    if dep.docker_container_id and dep.status == "running":
        try:
            def _get_logs():
                c = _get_docker().containers.get(dep.docker_container_id)
                return c.logs(stdout=True, stderr=True, tail=100).decode("utf-8", errors="replace")
            runtime_logs = await asyncio.to_thread(_get_logs)
        except Exception:
            pass

    return {
        "build_log": dep.build_log or "",
        "runtime_log": runtime_logs,
    }


@router.post("/projects/{deploy_id}/redeploy")
async def redeploy(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:RunInstance")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)
    project_dir = DEPLOYS_DIR / deploy_id

    if not project_dir.exists():
        raise HTTPException(status_code=400, detail="Project files no longer exist. Upload again.")

    # Stop existing container
    if dep.docker_container_id:
        try:
            await asyncio.to_thread(
                lambda: _get_docker().containers.get(dep.docker_container_id).remove(force=True)
            )
        except Exception:
            pass

    dep.status = "queued"
    dep.build_log = ""
    dep.docker_container_id = None
    await db.flush()

    info = _detect_project(project_dir)
    asyncio.create_task(_build_and_deploy(deploy_id, project_dir, info))

    return _deploy_to_dict(dep)


@router.post("/projects/{deploy_id}/stop")
async def stop_deployment(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:StopInstance")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)
    if dep.docker_container_id:
        try:
            await asyncio.to_thread(
                lambda: _get_docker().containers.get(dep.docker_container_id).stop(timeout=10)
            )
        except Exception:
            pass
    dep.status = "stopped"
    await db.flush()
    return _deploy_to_dict(dep)


@router.post("/projects/{deploy_id}/start")
async def start_deployment(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:StartInstance")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)
    if not dep.docker_container_id:
        raise HTTPException(status_code=400, detail="No container. Redeploy instead.")
    try:
        await asyncio.to_thread(
            lambda: _get_docker().containers.get(dep.docker_container_id).start()
        )
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Container not found. Redeploy instead.")
    dep.status = "running"
    await db.flush()
    return _deploy_to_dict(dep)


@router.get("/projects/{deploy_id}/files")
async def list_deploy_files(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)
    project_dir = DEPLOYS_DIR / dep.id
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


@router.delete("/projects/{deploy_id}")
async def delete_deployment(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:TerminateInstance")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)

    # Remove Caddy route
    await _remove_caddy_route(deploy_id)

    # Remove container
    if dep.docker_container_id:
        try:
            await asyncio.to_thread(
                lambda: _get_docker().containers.get(dep.docker_container_id).remove(force=True)
            )
        except Exception:
            pass

    # Remove image
    if dep.docker_image_id:
        try:
            await asyncio.to_thread(
                lambda: _get_docker().images.remove(dep.docker_image_id, force=True)
            )
        except Exception:
            pass

    # Remove project files
    project_dir = DEPLOYS_DIR / dep.id
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)

    await db.delete(dep)
    await db.flush()
    return {"detail": "Deployment deleted"}


@router.post("/projects/github")
async def deploy_from_github(
    name: str = Form(...),
    github_repo: str = Form(...),
    github_branch: str = Form("main"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deploy a GitHub repository by downloading its ZIP archive."""
    if not user.github_token:
        raise HTTPException(status_code=400, detail="GitHub not connected")

    port = _allocate_port()
    webhook_secret = _secrets.token_hex(32)

    dep = Deployment(
        name=name,
        owner_id=user.id,
        status="uploading",
        port=port,
        github_repo=github_repo,
        github_branch=github_branch,
        webhook_secret=webhook_secret,
    )
    db.add(dep)
    await db.flush()
    deploy_id = dep.id

    project_dir = DEPLOYS_DIR / deploy_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Download repo as ZIP from GitHub API
    try:
        zip_bytes = await _download_github_zip(user.github_token, github_repo, github_branch)
    except Exception as e:
        dep.status = "failed"
        dep.build_log = f"Failed to download repository: {e}"
        await db.flush()
        raise HTTPException(status_code=500, detail=f"Failed to download repo: {e}")

    _extract_zip(zip_bytes, project_dir)

    # Register GitHub webhook
    webhook_id = await _register_github_webhook(user.github_token, github_repo, webhook_secret)
    if webhook_id:
        dep.github_webhook_id = webhook_id
    await db.flush()

    # Start background build
    info = _detect_project(project_dir)
    asyncio.create_task(_build_and_deploy(deploy_id, project_dir, info))
    return _deploy_to_dict(dep)


async def _download_github_zip(token: str, repo: str, branch: str) -> bytes:
    """Download repository ZIP from GitHub API."""
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
    """Create a push webhook on the GitHub repo. Returns webhook ID or None."""
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


async def github_redeploy(deploy_id: str):
    """Re-download from GitHub and rebuild. Called by webhook handler."""
    async with async_session() as db:
        result = await db.execute(select(Deployment).where(Deployment.id == deploy_id))
        dep = result.scalar_one_or_none()
        if not dep or not dep.github_repo:
            return

        # Get owner's GitHub token
        result2 = await db.execute(select(User).where(User.id == dep.owner_id))
        owner = result2.scalar_one_or_none()
        if not owner or not owner.github_token:
            return

        dep.status = "uploading"
        dep.build_log = "Auto-redeploy triggered by GitHub push...\n"
        await db.commit()

    project_dir = DEPLOYS_DIR / deploy_id
    shutil.rmtree(project_dir, ignore_errors=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        zip_bytes = await _download_github_zip(owner.github_token, dep.github_repo, dep.github_branch or "main")
    except Exception as e:
        async with async_session() as db:
            result = await db.execute(select(Deployment).where(Deployment.id == deploy_id))
            dep2 = result.scalar_one_or_none()
            if dep2:
                dep2.status = "failed"
                dep2.build_log = f"Failed to download for redeploy: {e}"
                await db.commit()
        return

    _extract_zip(zip_bytes, project_dir)
    info = _detect_project(project_dir)
    await _build_and_deploy(deploy_id, project_dir, info)


# ── Tunnel ───────────────────────────────────────────────────────────

_deploy_tunnels: dict[str, dict] = {}  # deploy_id → {container_id, url}


@router.post("/projects/{deploy_id}/tunnel/start")
async def start_deploy_tunnel(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db)
    if dep.status != "running":
        raise HTTPException(status_code=400, detail="Deployment must be running")
    if not dep.port:
        raise HTTPException(status_code=400, detail="Deployment has no port assigned")

    _stop_deploy_tunnel(deploy_id)

    def _start_and_wait():
        import time
        container = _get_docker().containers.run(
            "cloudflare/cloudflared:latest",
            command=["tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{dep.port}"],
            network_mode="host",
            detach=True,
            name=f"awsclone-deploy-tunnel-{deploy_id}",
            labels={"awsclone": "true", "tunnel_for": deploy_id},
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

    _deploy_tunnels[deploy_id] = {"container_id": container.id, "url": url}
    return {"tunnel_url": url}


@router.delete("/projects/{deploy_id}/tunnel/stop")
async def stop_deploy_tunnel(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    await _get_deploy(deploy_id, db)
    if deploy_id not in _deploy_tunnels:
        raise HTTPException(status_code=404, detail="No active tunnel for this deployment")
    _stop_deploy_tunnel(deploy_id)
    return {"detail": "Tunnel stopped"}


def _stop_deploy_tunnel(deploy_id: str):
    if deploy_id in _deploy_tunnels:
        try:
            c = _get_docker().containers.get(_deploy_tunnels[deploy_id]["container_id"])
            c.remove(force=True)
        except Exception:
            pass
        del _deploy_tunnels[deploy_id]
    try:
        c = _get_docker().containers.get(f"awsclone-deploy-tunnel-{deploy_id}")
        c.remove(force=True)
    except Exception:
        pass


# ── Helpers ──────────────────────────────────────────────────────────

async def _get_deploy(deploy_id: str, db: AsyncSession) -> Deployment:
    result = await db.execute(select(Deployment).where(Deployment.id == deploy_id))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return dep


def _deploy_to_dict(dep: Deployment) -> dict:
    url = None
    if dep.port and SERVER_PUBLIC_IP:
        url = f"http://{SERVER_PUBLIC_IP}/deploy/{dep.id}/"

    tunnel = _deploy_tunnels.get(dep.id)

    return {
        "id": dep.id,
        "name": dep.name,
        "owner_id": dep.owner_id,
        "project_type": dep.project_type,
        "project_label": dep.project_label,
        "status": dep.status,
        "port": dep.port,
        "url": url,
        "tunnel_url": tunnel["url"] if tunnel else None,
        "github_repo": dep.github_repo,
        "github_branch": dep.github_branch,
        "github_webhook_id": dep.github_webhook_id,
        "created_at": dep.created_at.isoformat() if dep.created_at else None,
        "updated_at": dep.updated_at.isoformat() if dep.updated_at else None,
    }
