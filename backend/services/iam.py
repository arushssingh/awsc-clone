import json
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import require_permission, hash_password
from database import (
    User, Role, Policy, ApiKey, UserRole, RolePolicy,
    get_db, generate_id,
)

router = APIRouter(prefix="/api/v1/iam", tags=["iam"])


# ── Schemas ──────────────────────────────────────────────────────────────

class CreateRoleRequest(BaseModel):
    name: str
    description: Optional[str] = None


class CreatePolicyRequest(BaseModel):
    name: str
    document: str  # JSON string


class UpdatePolicyRequest(BaseModel):
    name: Optional[str] = None
    document: Optional[str] = None


class CreateApiKeyRequest(BaseModel):
    description: Optional[str] = None


class AssignRoleRequest(BaseModel):
    user_id: str
    role_id: str


class AttachPolicyRequest(BaseModel):
    role_id: str
    policy_id: str


class UpdateUserRequest(BaseModel):
    is_active: Optional[bool] = None
    email: Optional[str] = None


# ── Users ────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    user: User = Depends(require_permission("iam:ListUsers")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).options(selectinload(User.roles))
    )
    users = result.scalars().all()
    return [_user_to_dict(u) for u in users]


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    user: User = Depends(require_permission("iam:GetUser")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_dict(target)


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    user: User = Depends(require_permission("iam:UpdateUser")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.is_root and body.is_active is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate root user")

    if body.is_active is not None:
        target.is_active = body.is_active
    if body.email is not None:
        target.email = body.email

    await db.flush()
    return _user_to_dict(target)


# ── Roles ────────────────────────────────────────────────────────────────

@router.get("/roles")
async def list_roles(
    user: User = Depends(require_permission("iam:ListRoles")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Role).options(selectinload(Role.policies))
    )
    roles = result.scalars().all()
    return [_role_to_dict(r) for r in roles]


@router.get("/roles/{role_id}")
async def get_role(
    role_id: str,
    user: User = Depends(require_permission("iam:GetRole")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Role).options(selectinload(Role.policies)).where(Role.id == role_id)
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return _role_to_dict(role)


@router.post("/roles")
async def create_role(
    body: CreateRoleRequest,
    user: User = Depends(require_permission("iam:CreateRole")),
    db: AsyncSession = Depends(get_db),
):
    # Check uniqueness
    result = await db.execute(select(Role).where(Role.name == body.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Role name already exists")

    role = Role(
        id=generate_id(),
        name=body.name,
        description=body.description,
    )
    db.add(role)
    await db.flush()
    return _role_to_dict(role)


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: str,
    user: User = Depends(require_permission("iam:DeleteRole")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    await db.delete(role)
    return {"detail": "Role deleted"}


# ── Policies ─────────────────────────────────────────────────────────────

@router.get("/policies")
async def list_policies(
    user: User = Depends(require_permission("iam:ListPolicies")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Policy))
    policies = result.scalars().all()
    return [_policy_to_dict(p) for p in policies]


@router.get("/policies/{policy_id}")
async def get_policy(
    policy_id: str,
    user: User = Depends(require_permission("iam:GetPolicy")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Policy).where(Policy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _policy_to_dict(policy)


@router.post("/policies")
async def create_policy(
    body: CreatePolicyRequest,
    user: User = Depends(require_permission("iam:CreatePolicy")),
    db: AsyncSession = Depends(get_db),
):
    # Validate JSON document
    try:
        doc = json.loads(body.document)
        if "statements" not in doc:
            raise ValueError("Missing 'statements' key")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid policy document: {e}")

    # Check uniqueness
    result = await db.execute(select(Policy).where(Policy.name == body.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Policy name already exists")

    policy = Policy(
        id=generate_id(),
        name=body.name,
        document=body.document,
    )
    db.add(policy)
    await db.flush()
    return _policy_to_dict(policy)


@router.put("/policies/{policy_id}")
async def update_policy(
    policy_id: str,
    body: UpdatePolicyRequest,
    user: User = Depends(require_permission("iam:UpdatePolicy")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Policy).where(Policy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    if body.name is not None:
        policy.name = body.name
    if body.document is not None:
        try:
            doc = json.loads(body.document)
            if "statements" not in doc:
                raise ValueError("Missing 'statements' key")
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid policy document: {e}")
        policy.document = body.document

    await db.flush()
    return _policy_to_dict(policy)


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    user: User = Depends(require_permission("iam:DeletePolicy")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Policy).where(Policy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.delete(policy)
    return {"detail": "Policy deleted"}


# ── API Keys ─────────────────────────────────────────────────────────────

@router.get("/api-keys")
async def list_api_keys(
    user: User = Depends(require_permission("iam:ListApiKeys")),
    db: AsyncSession = Depends(get_db),
):
    if user.is_root:
        result = await db.execute(select(ApiKey))
    else:
        result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id))
    keys = result.scalars().all()
    return [_api_key_to_dict(k) for k in keys]


@router.post("/api-keys")
async def create_api_key(
    body: CreateApiKeyRequest,
    user: User = Depends(require_permission("iam:CreateApiKey")),
    db: AsyncSession = Depends(get_db),
):
    key_id = "ak_" + secrets.token_hex(8)
    key_secret = secrets.token_urlsafe(32)

    api_key = ApiKey(
        id=generate_id(),
        user_id=user.id,
        key_id=key_id,
        key_secret_hash=hash_password(key_secret),
        description=body.description,
    )
    db.add(api_key)
    await db.flush()

    # Return the secret only on creation
    return {
        "key_id": key_id,
        "key_secret": key_secret,
        "description": body.description,
    }


@router.delete("/api-keys/{key_id}")
async def delete_api_key(
    key_id: str,
    user: User = Depends(require_permission("iam:DeleteApiKey")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ApiKey).where(ApiKey.key_id == key_id))
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Non-root users can only delete their own keys
    if not user.is_root and api_key.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    await db.delete(api_key)
    return {"detail": "API key revoked"}


# ── Role Assignments ─────────────────────────────────────────────────────

@router.post("/users/assign-role")
async def assign_role_to_user(
    body: AssignRoleRequest,
    user: User = Depends(require_permission("iam:AssignRole")),
    db: AsyncSession = Depends(get_db),
):
    # Verify user and role exist
    result = await db.execute(select(User).where(User.id == body.user_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(select(Role).where(Role.id == body.role_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Role not found")

    # Check if already assigned
    result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == body.user_id,
            UserRole.role_id == body.role_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Role already assigned to user")

    db.add(UserRole(user_id=body.user_id, role_id=body.role_id))
    await db.flush()
    return {"detail": "Role assigned"}


@router.post("/users/remove-role")
async def remove_role_from_user(
    body: AssignRoleRequest,
    user: User = Depends(require_permission("iam:AssignRole")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == body.user_id,
            UserRole.role_id == body.role_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    await db.delete(assignment)
    return {"detail": "Role removed"}


# ── Policy Attachments ───────────────────────────────────────────────────

@router.post("/roles/attach-policy")
async def attach_policy_to_role(
    body: AttachPolicyRequest,
    user: User = Depends(require_permission("iam:AttachPolicy")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Role).where(Role.id == body.role_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Role not found")

    result = await db.execute(select(Policy).where(Policy.id == body.policy_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Policy not found")

    result = await db.execute(
        select(RolePolicy).where(
            RolePolicy.role_id == body.role_id,
            RolePolicy.policy_id == body.policy_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Policy already attached to role")

    db.add(RolePolicy(role_id=body.role_id, policy_id=body.policy_id))
    await db.flush()
    return {"detail": "Policy attached"}


@router.post("/roles/detach-policy")
async def detach_policy_from_role(
    body: AttachPolicyRequest,
    user: User = Depends(require_permission("iam:DetachPolicy")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RolePolicy).where(
            RolePolicy.role_id == body.role_id,
            RolePolicy.policy_id == body.policy_id,
        )
    )
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise HTTPException(status_code=404, detail="Policy attachment not found")

    await db.delete(attachment)
    return {"detail": "Policy detached"}


# ── Helpers ──────────────────────────────────────────────────────────────

def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_root": user.is_root,
        "is_active": user.is_active,
        "roles": [r.name for r in user.roles] if user.roles else [],
        "created_at": user.created_at.isoformat(),
    }


def _role_to_dict(role: Role) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "policies": [p.name for p in role.policies] if role.policies else [],
        "created_at": role.created_at.isoformat(),
    }


def _policy_to_dict(policy: Policy) -> dict:
    doc = {}
    try:
        doc = json.loads(policy.document)
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "id": policy.id,
        "name": policy.name,
        "document": doc,
        "created_at": policy.created_at.isoformat(),
    }


def _api_key_to_dict(key: ApiKey) -> dict:
    return {
        "key_id": key.key_id,
        "user_id": key.user_id,
        "description": key.description,
        "is_active": key.is_active,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "created_at": key.created_at.isoformat(),
    }
