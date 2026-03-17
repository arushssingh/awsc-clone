import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from database import init_db, Instance, async_session
from auth import router as auth_router
from services.ec2 import router as ec2_router
from services.vpc import router as vpc_router
from services.s3 import router as s3_router
from services.lambda_ import router as lambda_router
from services.route53 import router as route53_router
from services.cloudwatch import (
    router as cloudwatch_router,
    metrics_collector,
    alarm_evaluator,
    metrics_cleanup,
)
from services.iam import router as iam_router
from services.deploy import router as deploy_router
from services.github import router as github_router


# ── Reconciliation daemon ─────────────────────────────────────────────────

async def reconcile_instances():
    """On startup, sync instance states with actual Docker containers."""
    try:
        import docker as docker_lib
        d = docker_lib.from_env()
    except Exception:
        return

    async with async_session() as db:
        result = await db.execute(
            select(Instance).where(Instance.state.in_(["running", "pending", "stopping"]))
        )
        instances = result.scalars().all()

        # Check all containers in parallel threads
        async def _check_container(inst):
            if not inst.docker_container_id:
                return inst, "stopped"
            try:
                status = await asyncio.to_thread(
                    lambda: d.containers.get(inst.docker_container_id).status
                )
                return inst, status if status == "running" else "stopped"
            except Exception:
                return inst, "stopped"

        results = await asyncio.gather(*[_check_container(inst) for inst in instances])
        for inst, status in results:
            if status != "running":
                inst.state = "stopped"
        await db.commit()

    # Clean up stale Traefik subdomain routes
    async with async_session() as db:
        from services.traefik import remove_subdomain_route
        result = await db.execute(
            select(Instance).where(
                Instance.subdomain.isnot(None),
                Instance.state.in_(["terminated", "stopped"]),
            )
        )
        for inst in result.scalars().all():
            remove_subdomain_route(inst.subdomain)


# ── Rate limiting (sliding window counter — O(1) per request) ────────────

_RATE_WINDOW = 60       # seconds
_RATE_MAX = 120         # requests per window per IP
# Each entry: (prev_count, curr_count, window_start)
_rate_buckets: dict[str, list] = {}
_last_cleanup = 0.0


def _check_rate_limit(ip: str) -> bool:
    global _last_cleanup
    now = time.monotonic()

    # Cleanup stale IPs every 5 minutes
    if now - _last_cleanup > 300:
        cutoff = now - _RATE_WINDOW * 2
        stale = [k for k, v in _rate_buckets.items() if v[2] < cutoff]
        for k in stale:
            del _rate_buckets[k]
        _last_cleanup = now

    bucket = _rate_buckets.get(ip)
    if bucket is None:
        _rate_buckets[ip] = [0, 1, now]
        return True

    prev_count, curr_count, window_start = bucket

    # Slide windows
    elapsed = now - window_start
    if elapsed >= _RATE_WINDOW * 2:
        # Both windows expired — reset
        _rate_buckets[ip] = [0, 1, now]
        return True
    elif elapsed >= _RATE_WINDOW:
        # Current window becomes previous, start new current
        prev_count = curr_count
        curr_count = 0
        window_start = window_start + _RATE_WINDOW
        elapsed = now - window_start

    # Weighted estimate of requests in the sliding window
    weight = 1.0 - (elapsed / _RATE_WINDOW)
    estimated = prev_count * weight + curr_count

    if estimated >= _RATE_MAX:
        _rate_buckets[ip] = [prev_count, curr_count, window_start]
        return False

    curr_count += 1
    _rate_buckets[ip] = [prev_count, curr_count, window_start]
    return True


# ── App lifecycle ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await reconcile_instances()

    tasks = [
        asyncio.create_task(metrics_collector()),
        asyncio.create_task(alarm_evaluator()),
        asyncio.create_task(metrics_cleanup()),
    ]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# ── Application ───────────────────────────────────────────────────────────

app = FastAPI(
    title="folateCloud",
    description="Self-hosted lightweight cloud platform",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path == "/api/v1/health":
        return await call_next(request)

    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})
    return await call_next(request)


_cors_origins = os.getenv("CORS_ORIGINS", "")
_allowed_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]

if _allowed_origins:
    # Explicit origin list: enable credentials for cookie-based auth
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # No CORS_ORIGINS set: allow all origins without credentials (dev/same-origin only)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Routers ──────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(ec2_router)
app.include_router(vpc_router)
app.include_router(s3_router)
app.include_router(lambda_router)
app.include_router(route53_router)
app.include_router(cloudwatch_router)
app.include_router(iam_router)
app.include_router(deploy_router)
app.include_router(github_router)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/v1/config")
async def public_config():
    from config import BASE_DOMAIN
    return {"base_domain": BASE_DOMAIN}
