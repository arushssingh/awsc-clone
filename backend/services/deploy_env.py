"""Environment variable management endpoints for deployments."""

import json
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission
from database import User, get_db

from services.deploy import _get_deploy
from services.env_validation import (
    validate_ai_api_key,
    validate_supabase_anon_key,
    validate_supabase_url,
)

router = APIRouter(prefix="/api/v1/deploy", tags=["deploy-env"])

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_ENV_VARS = 50

KeyType = Literal["ai_api_key", "supabase_url", "supabase_anon_key", "custom"]


# ── Schemas ───────────────────────────────────────────────────────────

class EnvVar(BaseModel):
    key: str = Field(..., max_length=256)
    value: str = Field(..., max_length=65536)
    key_type: KeyType


class EnvVarsUpdate(BaseModel):
    env_vars: list[EnvVar] = Field(..., max_length=_MAX_ENV_VARS)


class EnvVarValidateRequest(BaseModel):
    key: str = Field(..., max_length=256)
    value: str = Field(..., max_length=65536)
    key_type: KeyType
    supabase_url: Optional[str] = Field(None, max_length=2048)


# ── Helpers ───────────────────────────────────────────────────────────

def _parse_env_vars(raw: str | None) -> list[dict]:
    """Parse the env_vars JSON column, handling both {} and [] formats."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        # Legacy format: convert {"KEY": "val"} to list format
        return [{"key": k, "value": v, "key_type": "custom"} for k, v in parsed.items()]
    return []


def _mask_value(value: str) -> str:
    """Mask the middle of a secret value for display."""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/projects/{deploy_id}/env")
async def get_env_vars(
    deploy_id: str,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db, user)
    env_vars = _parse_env_vars(dep.env_vars)

    # Only return masked values — full values are never sent over the wire
    return {
        "env_vars": [
            {
                "key": item.get("key", ""),
                "value": "",
                "display_value": _mask_value(item.get("value", "")),
                "key_type": item.get("key_type", "custom"),
                "has_value": bool(item.get("value")),
            }
            for item in env_vars
        ]
    }


@router.put("/projects/{deploy_id}/env")
async def update_env_vars(
    deploy_id: str,
    body: EnvVarsUpdate,
    user: User = Depends(require_permission("ec2:RunInstance")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db, user)

    # Validate key names
    seen_keys: set[str] = set()
    for env_var in body.env_vars:
        key = env_var.key.strip()
        if not key:
            raise HTTPException(status_code=400, detail="Key name cannot be empty")
        if not _KEY_PATTERN.match(key):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid key name '{key}'. Use only letters, digits, and underscores.",
            )
        if key in seen_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate key '{key}'",
            )
        seen_keys.add(key)

    # Preserve existing values for keys where user sent empty value (unchanged)
    existing = {item.get("key"): item.get("value", "") for item in _parse_env_vars(dep.env_vars)}

    env_list = []
    for ev in body.env_vars:
        key = ev.key.strip()
        value = ev.value if ev.value else existing.get(key, "")
        env_list.append({"key": key, "value": value, "key_type": ev.key_type})

    dep.env_vars = json.dumps(env_list)
    await db.flush()

    return {"detail": "Environment variables saved", "count": len(env_list)}


@router.post("/projects/{deploy_id}/env/validate")
async def validate_env_var(
    deploy_id: str,
    body: EnvVarValidateRequest,
    user: User = Depends(require_permission("ec2:DescribeInstances")),
    db: AsyncSession = Depends(get_db),
):
    dep = await _get_deploy(deploy_id, db, user)

    if body.key_type == "supabase_url":
        valid, msg = await validate_supabase_url(body.value)

    elif body.key_type == "supabase_anon_key":
        # Use URL from request body first, fall back to saved env vars
        url = body.supabase_url or ""
        if not url:
            env_vars = _parse_env_vars(dep.env_vars)
            url = next(
                (v["value"] for v in env_vars if v.get("key") == "VITE_SUPABASE_URL"),
                "",
            )
        if not url:
            return {
                "valid": False,
                "message": "Set VITE_SUPABASE_URL first before validating the anon key",
            }
        valid, msg = await validate_supabase_anon_key(url, body.value)

    elif body.key_type == "ai_api_key":
        valid, msg = await validate_ai_api_key(body.value)

    else:
        valid, msg = True, "Custom keys are not validated"

    return {"valid": valid, "message": msg}
