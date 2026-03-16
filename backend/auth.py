import json
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import os

from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRY_HOURS
from database import User, Role, Policy, UserRole, RolePolicy, get_db, generate_id

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

COOKIE_NAME = "access_token"
COOKIE_MAX_AGE = JWT_EXPIRY_HOURS * 3600
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


# ── Password hashing ────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ── JWT ──────────────────────────────────────────────────────────────────

def create_token(user_id: str, username: str, roles: list[str]) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "roles": roles,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ── Dependencies ─────────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the current user from JWT (cookie or header) or API key."""
    # Try Bearer header first, then httpOnly cookie
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    else:
        token = request.cookies.get(COOKIE_NAME)

    if token:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        return user

    if x_api_key:
        parts = x_api_key.split(":", 1)
        if len(parts) != 2:
            raise HTTPException(status_code=401, detail="Invalid API key format")
        from database import ApiKey
        key_id, key_secret = parts
        result = await db.execute(
            select(ApiKey).where(ApiKey.key_id == key_id, ApiKey.is_active == True)
        )
        api_key = result.scalar_one_or_none()
        if not api_key or not verify_password(key_secret, api_key.key_secret_hash):
            raise HTTPException(status_code=401, detail="Invalid API key")
        api_key.last_used_at = datetime.utcnow()
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == api_key.user_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        return user

    raise HTTPException(status_code=401, detail="Authentication required")


def require_permission(action: str, resource: str = "*"):
    """Dependency factory that checks IAM permissions before allowing access."""
    async def checker(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if current_user.is_root:
            return current_user

        policies = await _load_user_policies(current_user.id, db)
        if not _evaluate_policies(policies, action, resource):
            raise HTTPException(status_code=403, detail=f"Access denied: {action}")
        return current_user
    return checker


async def _load_user_policies(user_id: str, db: AsyncSession) -> list[dict]:
    """Load all policy documents for a user via their roles."""
    result = await db.execute(
        select(Policy)
        .join(RolePolicy, Policy.id == RolePolicy.policy_id)
        .join(UserRole, RolePolicy.role_id == UserRole.role_id)
        .where(UserRole.user_id == user_id)
    )
    policies = result.scalars().all()
    docs = []
    for p in policies:
        try:
            docs.append(json.loads(p.document))
        except json.JSONDecodeError:
            continue
    return docs


def _evaluate_policies(policies: list[dict], action: str, resource: str) -> bool:
    """
    AWS-style policy evaluation:
    1. Check for explicit Deny → deny
    2. Check for explicit Allow → allow
    3. Default → deny
    """
    has_allow = False

    for doc in policies:
        for stmt in doc.get("statements", []):
            effect = stmt.get("effect", "").lower()
            actions = stmt.get("actions", [])
            resources = stmt.get("resources", [])

            if not _action_matches(action, actions):
                continue
            if not _resource_matches(resource, resources):
                continue

            if effect == "deny":
                return False
            if effect == "allow":
                has_allow = True

    return has_allow


def _action_matches(action: str, patterns: list[str]) -> bool:
    """Check if an action matches any pattern (supports * wildcard)."""
    for pattern in patterns:
        if pattern == "*":
            return True
        if pattern.endswith(":*"):
            if action.startswith(pattern[:-1]):
                return True
        if pattern == action:
            return True
    return False


def _resource_matches(resource: str, patterns: list[str]) -> bool:
    """Check if a resource matches any pattern."""
    for pattern in patterns:
        if pattern == "*":
            return True
        if pattern == resource:
            return True
    return False


# ── Default role for new users ────────────────────────────────────────────

DEFAULT_POLICY_DOCUMENT = json.dumps({
    "statements": [
        {
            "effect": "allow",
            "actions": [
                "ec2:RunInstance",
                "ec2:DescribeInstances",
                "ec2:StartInstance",
                "ec2:StopInstance",
                "ec2:TerminateInstance",
                "s3:ListBuckets",
                "s3:CreateBucket",
                "s3:DeleteBucket",
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "vpc:DescribeVpcs",
                "vpc:CreateVpc",
                "vpc:DeleteVpc",
                "vpc:AttachInstance",
                "vpc:DetachInstance",
                "lambda:ListFunctions",
                "lambda:GetFunction",
                "lambda:CreateFunction",
                "lambda:UpdateFunction",
                "lambda:DeleteFunction",
                "lambda:InvokeFunction",
                "route53:ListDomains",
                "route53:DescribeDomain",
                "route53:CreateDomain",
                "route53:DeleteDomain",
                "cloudwatch:PutMetricAlarm",
                "cloudwatch:GetMetricStatistics",
            ],
            "resources": ["*"],
        }
    ]
})


async def _ensure_default_role(user_id: str, db: AsyncSession) -> list[str]:
    """Create (or reuse) a default role+policy and assign it to the user."""
    default_role_name = "DefaultUserRole"

    # Check if role already exists
    result = await db.execute(select(Role).where(Role.name == default_role_name))
    role = result.scalar_one_or_none()

    if not role:
        # Create default policy
        policy = Policy(
            id=generate_id(),
            name="DefaultUserPolicy",
            document=DEFAULT_POLICY_DOCUMENT,
        )
        db.add(policy)

        # Create default role
        role = Role(
            id=generate_id(),
            name=default_role_name,
            description="Default role assigned to new users with basic service permissions",
        )
        db.add(role)
        await db.flush()

        # Attach policy to role
        db.add(RolePolicy(role_id=role.id, policy_id=policy.id))
        await db.flush()

    # Assign role to user
    db.add(UserRole(user_id=user_id, role_id=role.id))
    await db.flush()

    return [default_role_name]


# ── Request/Response schemas ─────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: Optional[str] = None
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    expires_at: str
    user_id: str
    username: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: Optional[str]
    is_root: bool
    roles: list[str]
    created_at: str


# ── Cookie helpers ───────────────────────────────────────────────────────

def _set_auth_cookie(response: JSONResponse, token: str) -> None:
    """Set httpOnly cookie with the JWT token."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookie(response: JSONResponse) -> None:
    """Clear the auth cookie."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )


# ── Auth endpoints ───────────────────────────────────────────────────────

@router.post("/register")
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check if username already exists
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")

    if body.email:
        result = await db.execute(select(User).where(User.email == body.email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email already registered")

    # Check if this is the first user (make them root)
    result = await db.execute(select(User))
    is_first_user = result.first() is None

    user = User(
        id=generate_id(),
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        is_root=is_first_user,
    )
    db.add(user)
    await db.flush()

    # Assign default role with basic permissions for non-root users
    role_names = []
    if not is_first_user:
        role_names = await _ensure_default_role(user.id, db)

    expires = datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    token = create_token(user.id, user.username, role_names)

    response = JSONResponse(content={
        "expires_at": expires.isoformat(),
        "user_id": user.id,
        "username": user.username,
    })
    _set_auth_cookie(response, token)
    return response


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.username == body.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    role_names = [r.name for r in user.roles]

    # Retroactively assign default role to existing users with no roles
    if not user.is_root and len(role_names) == 0:
        role_names = await _ensure_default_role(user.id, db)

    expires = datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    token = create_token(user.id, user.username, role_names)

    response = JSONResponse(content={
        "expires_at": expires.isoformat(),
        "user_id": user.id,
        "username": user.username,
    })
    _set_auth_cookie(response, token)
    return response


@router.post("/refresh")
async def refresh(current_user: User = Depends(get_current_user)):
    role_names = [r.name for r in current_user.roles]
    expires = datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    token = create_token(current_user.id, current_user.username, role_names)

    response = JSONResponse(content={
        "expires_at": expires.isoformat(),
        "user_id": current_user.id,
        "username": current_user.username,
    })
    _set_auth_cookie(response, token)
    return response


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """Clear the auth cookie."""
    response = JSONResponse(content={"detail": "Logged out"})
    _clear_auth_cookie(response)
    return response


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_root=current_user.is_root,
        roles=[r.name for r in current_user.roles],
        created_at=current_user.created_at.isoformat(),
    )
