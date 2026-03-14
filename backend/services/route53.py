import json
import re
import socket
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission
from config import SERVER_PUBLIC_IP
from database import Domain, Instance, User, get_db, generate_id
from services.traefik import write_domain_route, remove_domain_route

router = APIRouter(prefix="/api/v1/route53", tags=["route53"])


# ── Schemas ──────────────────────────────────────────────────────────────

class CreateDomainRequest(BaseModel):
    domain: str
    target_type: str  # instance | external
    target_id: Optional[str] = None
    target_address: Optional[str] = None
    ssl_enabled: bool = True


class UpdateDomainRequest(BaseModel):
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    target_address: Optional[str] = None
    ssl_enabled: Optional[bool] = None


# ── Resolve target to upstream address ───────────────────────────────────

async def _resolve_upstream(
    target_type: str,
    target_id: Optional[str],
    target_address: Optional[str],
    db: AsyncSession,
) -> str:
    """Resolve the domain target to a host:port upstream."""
    if target_type == "external":
        if not target_address:
            raise HTTPException(status_code=400, detail="target_address required for external type")
        # Strip protocol for upstream format
        addr = target_address
        for prefix in ("http://", "https://"):
            if addr.startswith(prefix):
                addr = addr[len(prefix):]
        return addr

    if target_type == "instance":
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id required for instance type")
        result = await db.execute(select(Instance).where(Instance.id == target_id))
        instance = result.scalar_one_or_none()
        if not instance:
            raise HTTPException(status_code=404, detail="Target instance not found")

        # Find the first mapped host port
        if instance.port_mappings:
            try:
                mappings = json.loads(instance.port_mappings)
                if mappings:
                    # Get the first host port
                    first_host_port = list(mappings.values())[0]
                    return f"localhost:{first_host_port}"
            except (json.JSONDecodeError, IndexError):
                pass

        raise HTTPException(
            status_code=400,
            detail="Instance has no port mappings. Expose a port when launching.",
        )

    raise HTTPException(status_code=400, detail=f"Invalid target_type: {target_type}")


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/domains")
async def list_domains(
    user: User = Depends(require_permission("route53:ListDomains")),
    db: AsyncSession = Depends(get_db),
):
    if user.is_root:
        result = await db.execute(select(Domain))
    else:
        result = await db.execute(select(Domain).where(Domain.owner_id == user.id))
    domains = result.scalars().all()
    return [_domain_to_dict(d) for d in domains]


@router.get("/domains/{domain_id}")
async def get_domain(
    domain_id: str,
    user: User = Depends(require_permission("route53:DescribeDomain")),
    db: AsyncSession = Depends(get_db),
):
    domain = await _get_domain(domain_id, db, user)
    return _domain_to_dict(domain)


@router.post("/domains")
async def create_domain(
    body: CreateDomainRequest,
    user: User = Depends(require_permission("route53:CreateDomain")),
    db: AsyncSession = Depends(get_db),
):
    # Validate domain format
    if not re.match(r'^[a-z0-9]([a-z0-9\-\.]{0,253}[a-z0-9])?$', body.domain.lower()):
        raise HTTPException(status_code=400, detail="Invalid domain name")

    # Check domain uniqueness
    result = await db.execute(select(Domain).where(Domain.domain == body.domain))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Domain already registered")

    # Resolve upstream
    upstream = await _resolve_upstream(body.target_type, body.target_id, body.target_address, db)

    domain_id = generate_id()
    route_id = f"domain-{domain_id}"

    # Write Traefik dynamic config file
    try:
        write_domain_route(route_id, body.domain, upstream)
        state = "active"
    except Exception:
        state = "pending"

    domain = Domain(
        id=domain_id,
        owner_id=user.id,
        domain=body.domain,
        target_type=body.target_type,
        target_id=body.target_id,
        target_address=upstream,
        ssl_enabled=body.ssl_enabled,
        caddy_route_id=route_id,
        state=state,
    )
    db.add(domain)
    await db.flush()

    resp = _domain_to_dict(domain)
    resp["instructions"] = (
        f"Point your domain's A record to your server's public IP"
        f"{(' (' + SERVER_PUBLIC_IP + ')') if SERVER_PUBLIC_IP else ''}. "
        f"Traefik will route traffic once DNS propagates."
    )
    return resp


@router.put("/domains/{domain_id}")
async def update_domain(
    domain_id: str,
    body: UpdateDomainRequest,
    user: User = Depends(require_permission("route53:UpdateDomain")),
    db: AsyncSession = Depends(get_db),
):
    domain = await _get_domain(domain_id, db, user)

    target_type = body.target_type or domain.target_type
    target_id = body.target_id if body.target_id is not None else domain.target_id
    target_address = body.target_address if body.target_address is not None else domain.target_address

    # Re-resolve upstream
    upstream = await _resolve_upstream(target_type, target_id, target_address, db)

    # Update Traefik route file
    try:
        write_domain_route(domain.caddy_route_id, domain.domain, upstream)
        domain.state = "active"
    except Exception:
        domain.state = "pending"

    domain.target_type = target_type
    domain.target_id = target_id
    domain.target_address = upstream
    if body.ssl_enabled is not None:
        domain.ssl_enabled = body.ssl_enabled
    domain.updated_at = datetime.utcnow()

    await db.flush()
    return _domain_to_dict(domain)


@router.delete("/domains/{domain_id}")
async def delete_domain(
    domain_id: str,
    user: User = Depends(require_permission("route53:DeleteDomain")),
    db: AsyncSession = Depends(get_db),
):
    domain = await _get_domain(domain_id, db, user)

    # Remove Traefik route file
    if domain.caddy_route_id:
        try:
            remove_domain_route(domain.caddy_route_id)
        except Exception:
            pass

    await db.delete(domain)
    return {"detail": "Domain removed"}


@router.post("/domains/{domain_id}/verify")
async def verify_domain_dns(
    domain_id: str,
    user: User = Depends(require_permission("route53:DescribeDomain")),
    db: AsyncSession = Depends(get_db),
):
    domain = await _get_domain(domain_id, db, user)

    try:
        resolved_ips = set()
        addr_info = socket.getaddrinfo(domain.domain, None, socket.AF_INET)
        for entry in addr_info:
            resolved_ips.add(entry[4][0])

        if not resolved_ips:
            return {
                "verified": False,
                "message": f"No A records found for {domain.domain}",
                "resolved_ips": [],
            }

        # Check against server public IP if configured
        if SERVER_PUBLIC_IP:
            matches = SERVER_PUBLIC_IP in resolved_ips
            if matches:
                domain.state = "active"
                await db.flush()
            return {
                "verified": matches,
                "message": (
                    "DNS is correctly configured!"
                    if matches
                    else f"DNS resolves to {resolved_ips}, but server IP is {SERVER_PUBLIC_IP}"
                ),
                "resolved_ips": list(resolved_ips),
                "expected_ip": SERVER_PUBLIC_IP,
            }

        # No server IP configured — just report what we found
        return {
            "verified": True,
            "message": f"Domain resolves to {resolved_ips}. Set SERVER_PUBLIC_IP in .env to enable full verification.",
            "resolved_ips": list(resolved_ips),
        }

    except socket.gaierror:
        return {
            "verified": False,
            "message": f"DNS lookup failed for {domain.domain}. Ensure an A record is configured.",
            "resolved_ips": [],
        }


@router.get("/domains/{domain_id}/ssl-status")
async def get_ssl_status(
    domain_id: str,
    user: User = Depends(require_permission("route53:DescribeDomain")),
    db: AsyncSession = Depends(get_db),
):
    domain = await _get_domain(domain_id, db, user)

    if not domain.ssl_enabled:
        return {"ssl_enabled": False, "status": "disabled"}

    return {
        "ssl_enabled": True,
        "status": "pending",
        "message": "SSL is handled by Cloudflare Tunnel. Ensure your tunnel is configured.",
        "domain": domain.domain,
    }


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_domain(domain_id: str, db: AsyncSession, user) -> Domain:
    result = await db.execute(select(Domain).where(Domain.id == domain_id))
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    if not user.is_root and domain.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Domain not found")
    return domain


def _domain_to_dict(domain: Domain) -> dict:
    return {
        "id": domain.id,
        "owner_id": domain.owner_id,
        "domain": domain.domain,
        "target_type": domain.target_type,
        "target_id": domain.target_id,
        "target_address": domain.target_address,
        "ssl_enabled": domain.ssl_enabled,
        "caddy_route_id": domain.caddy_route_id,
        "state": domain.state,
        "created_at": domain.created_at.isoformat(),
        "updated_at": domain.updated_at.isoformat(),
    }
