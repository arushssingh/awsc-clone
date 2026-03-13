import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from database import init_db, Instance, async_session
from auth import router as auth_router
from services.ec2 import router as ec2_router, restore_subdomain_routes
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
        for inst in result.scalars().all():
            if not inst.docker_container_id:
                inst.state = "stopped"
                continue
            try:
                container = d.containers.get(inst.docker_container_id)
                # "restarting" means the app is crash-looping — treat as stopped
                if container.status == "running":
                    pass  # keep existing state
                else:
                    inst.state = "stopped"
            except Exception:
                inst.state = "stopped"
        await db.commit()


# ── Rate limiting (fixed-window, auto-cleanup) ───────────────────────────

_RATE_WINDOW = 60       # seconds
_RATE_MAX = 120         # requests per window per IP
_rate_buckets: dict[str, list[float]] = {}
_last_cleanup = 0.0


def _check_rate_limit(ip: str) -> bool:
    global _last_cleanup
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW

    # Cleanup stale IPs every 5 minutes to prevent memory leak
    if now - _last_cleanup > 300:
        stale = [k for k, v in _rate_buckets.items() if not v or v[-1] < cutoff]
        for k in stale:
            del _rate_buckets[k]
        _last_cleanup = now

    timestamps = _rate_buckets.get(ip, [])
    timestamps = [t for t in timestamps if t > cutoff]

    if len(timestamps) >= _RATE_MAX:
        _rate_buckets[ip] = timestamps
        return False

    timestamps.append(now)
    _rate_buckets[ip] = timestamps
    return True


# ── App lifecycle ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await reconcile_instances()
    await restore_subdomain_routes()

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
    title="AWS Clone",
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
app.include_router(deploy_router)
app.include_router(github_router)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/v1/config")
async def public_config():
    from config import BASE_DOMAIN
    return {"base_domain": BASE_DOMAIN}
