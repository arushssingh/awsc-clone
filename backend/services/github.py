import hashlib
import hmac
import json
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_permission
from config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, PUBLIC_BASE_URL
from database import Deployment, Instance, User, async_session, get_db

router = APIRouter(prefix="/api/v1/github", tags=["github"])

# state → {user_id, instance_id} (short-lived, cleared after use)
_oauth_states: dict[str, dict] = {}


# ── OAuth ─────────────────────────────────────────────────────────────────

@router.get("/auth/url")
async def github_auth_url(
    instance_id: str | None = None,
    user: User = Depends(get_current_user),
):
    if not GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="GitHub OAuth not configured. Add GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET to .env",
        )
    state = secrets.token_urlsafe(32)
    # store user_id and optional instance_id so callback can redirect back
    _oauth_states[state] = {"user_id": user.id, "instance_id": instance_id}
    redirect_uri = f"{PUBLIC_BASE_URL}/api/v1/github/auth/callback"
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=repo"
        f"&state={state}"
    )
    return {"url": url}


@router.get("/auth/callback")
async def github_auth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    user_id = state_data["user_id"]
    instance_id = state_data.get("instance_id")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        data = resp.json()

    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail=f"GitHub OAuth failed: {data.get('error_description', data)}")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.github_token = access_token
    await db.flush()

    if instance_id:
        return RedirectResponse(url=f"/ec2/instances/{instance_id}?github=connected")
    return RedirectResponse(url="/ec2?github=connected")


@router.get("/status")
async def github_status(user: User = Depends(get_current_user)):
    if not user.github_token:
        return {"connected": False, "login": None}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {user.github_token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
    if resp.status_code != 200:
        return {"connected": False, "login": None}
    data = resp.json()
    return {"connected": True, "login": data.get("login"), "avatar_url": data.get("avatar_url")}


@router.delete("/disconnect")
async def github_disconnect(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user.id))
    u = result.scalar_one_or_none()
    if u:
        u.github_token = None
        await db.flush()
    return {"detail": "GitHub disconnected"}


# ── Repos & Branches ─────────────────────────────────────────────────────

@router.get("/repos")
async def list_repos(user: User = Depends(get_current_user)):
    if not user.github_token:
        raise HTTPException(status_code=400, detail="GitHub not connected")

    repos = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                "https://api.github.com/user/repos",
                headers={"Authorization": f"Bearer {user.github_token}", "Accept": "application/vnd.github+json"},
                params={"sort": "updated", "per_page": 100, "page": page, "affiliation": "owner,collaborator"},
                timeout=15,
            )
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            repos.extend([
                {
                    "full_name": r["full_name"],
                    "name": r["name"],
                    "private": r["private"],
                    "default_branch": r["default_branch"],
                    "description": r.get("description"),
                    "updated_at": r["updated_at"],
                }
                for r in data
            ])
            if len(data) < 100:
                break
            page += 1

    return repos


@router.get("/repos/{owner}/{repo}/branches")
async def list_branches(owner: str, repo: str, user: User = Depends(get_current_user)):
    if not user.github_token:
        raise HTTPException(status_code=400, detail="GitHub not connected")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/branches",
            headers={"Authorization": f"Bearer {user.github_token}", "Accept": "application/vnd.github+json"},
            params={"per_page": 100},
            timeout=15,
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository not found")

    return [b["name"] for b in resp.json()]


# ── Webhook ──────────────────────────────────────────────────────────────

@router.post("/webhook")
async def github_webhook(request: Request):
    body = await request.body()
    event = request.headers.get("X-GitHub-Event", "")
    sig_header = request.headers.get("X-Hub-Signature-256", "")

    if event != "push":
        return {"detail": "ignored"}

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    repo_full_name = payload.get("repository", {}).get("full_name", "")
    ref = payload.get("ref", "")
    branch = ref.replace("refs/heads/", "")

    if not repo_full_name or not branch:
        return {"detail": "no repo/branch"}

    import asyncio
    redeployed = []

    async with async_session() as db:
        # Check Deployments table
        result = await db.execute(
            select(Deployment).where(
                Deployment.github_repo == repo_full_name,
                Deployment.github_branch == branch,
            )
        )
        for dep in result.scalars().all():
            if not dep.webhook_secret or not sig_header:
                continue
            expected = "sha256=" + hmac.new(
                dep.webhook_secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig_header, expected):
                continue
            asyncio.create_task(_trigger_deploy_redeploy(dep.id))
            redeployed.append(f"deploy:{dep.id}")

        # Check Instances with a GitHub website deployed
        result2 = await db.execute(
            select(Instance).where(
                Instance.github_repo == repo_full_name,
                Instance.github_branch == branch,
            )
        )
        for inst in result2.scalars().all():
            if not inst.webhook_secret or not sig_header:
                continue
            expected = "sha256=" + hmac.new(
                inst.webhook_secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig_header, expected):
                continue
            asyncio.create_task(_trigger_instance_redeploy(inst.id))
            redeployed.append(f"instance:{inst.id}")

    return {"redeployed": redeployed}


async def _trigger_deploy_redeploy(deploy_id: str):
    from services.deploy import github_redeploy
    await github_redeploy(deploy_id)


async def _trigger_instance_redeploy(instance_id: str):
    from services.ec2 import instance_github_redeploy
    await instance_github_redeploy(instance_id)
