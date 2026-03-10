import asyncio
import time
from collections import defaultdict
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


# ── Reconciliation daemon ─────────────────────────────────────────────────

async def reconcile_instances():
    """
    On startup, sync instance states with actual Docker container states.
    Instances that no longer have a live container get marked 'stopped'.
    """
    try:
        import docker as docker_lib
        d = docker_lib.from_env()
    except Exception:
        return  # Docker not available (dev machine without Docker)

    async with async_session() as db:
        result = await db.execute(
            select(Instance).where(Instance.state.in_(["running", "pending", "stopping"]))
        )
        instances = result.scalars().all()

        for inst in instances:
            if not inst.docker_container_id:
                inst.state = "stopped"
                continue
            try:
                container = d.containers.get(inst.docker_container_id)
                if container.status not in ("running", "restarting"):
                    inst.state = "stopped"
            except Exception:
                inst.state = "stopped"

        await db.commit()


# ── Rate limiting ─────────────────────────────────────────────────────────

# Simple fixed-window rate limiter: 120 requests per minute per IP
_rate_limit_window = 60      # seconds
_rate_limit_max = 120        # requests per window
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str) -> bool:
    """Returns True if the request is allowed."""
    now = time.monotonic()
    window_start = now - _rate_limit_window
    timestamps = _rate_buckets[ip]
    # Drop timestamps outside the window
    _rate_buckets[ip] = [t for t in timestamps if t > window_start]
    if len(_rate_buckets[ip]) >= _rate_limit_max:
        return False
    _rate_buckets[ip].append(now)
    return True


# ── App lifecycle ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables
    await init_db()

    # Reconcile EC2 instance states with Docker reality
    await reconcile_instances()

    # Launch background tasks
    tasks = [
        asyncio.create_task(metrics_collector()),
        asyncio.create_task(alarm_evaluator()),
        asyncio.create_task(metrics_cleanup()),
    ]

    yield

    # Shutdown: cancel background tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# ── Application ───────────────────────────────────────────────────────────

app = FastAPI(
    title="AWS Clone",
    description="Self-hosted lightweight cloud platform",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Skip rate limiting for health checks
    if request.url.path == "/api/v1/health":
        return await call_next(request)

    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please slow down."},
        )
    return await call_next(request)


# CORS — allow frontend dev server and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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


# ── Health check ─────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
