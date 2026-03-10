import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional

import docker
import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text, delete
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_permission
from config import METRICS_COLLECT_INTERVAL, METRICS_RETENTION_DAYS
from database import Metric, Alarm, Instance, User, get_db, generate_id, async_session

router = APIRouter(prefix="/api/v1/cloudwatch", tags=["cloudwatch"])

# Lazy Docker client
_docker_client = None


def get_docker():
    global _docker_client
    if _docker_client is None:
        try:
            _docker_client = docker.from_env()
        except Exception:
            return None
    return _docker_client


# Unit mapping
UNITS = {
    "cpu_percent": "Percent",
    "memory_percent": "Percent",
    "memory_used_mb": "Megabytes",
    "disk_percent": "Percent",
    "disk_used_gb": "Gigabytes",
    "network_bytes_sent": "Bytes",
    "network_bytes_recv": "Bytes",
    "container_cpu_percent": "Percent",
    "container_memory_mb": "Megabytes",
}


# ── Schemas ──────────────────────────────────────────────────────────────

class CreateAlarmRequest(BaseModel):
    name: str
    metric_name: str
    comparison: str  # gt | lt | gte | lte
    threshold: float
    period_seconds: int = 300


class UpdateAlarmRequest(BaseModel):
    name: Optional[str] = None
    metric_name: Optional[str] = None
    comparison: Optional[str] = None
    threshold: Optional[float] = None
    period_seconds: Optional[int] = None


# ── Background tasks ────────────────────────────────────────────────────

async def metrics_collector():
    """Background task: collect system + container metrics every interval."""
    while True:
        try:
            # System metrics
            metrics = {
                "cpu_percent": psutil.cpu_percent(interval=1),
                "memory_percent": psutil.virtual_memory().percent,
                "memory_used_mb": psutil.virtual_memory().used / (1024 * 1024),
                "disk_percent": psutil.disk_usage("/").percent,
                "disk_used_gb": psutil.disk_usage("/").used / (1024 ** 3),
                "network_bytes_sent": psutil.net_io_counters().bytes_sent,
                "network_bytes_recv": psutil.net_io_counters().bytes_recv,
            }

            async with async_session() as db:
                now = datetime.utcnow()
                for name, value in metrics.items():
                    db.add(Metric(
                        timestamp=now,
                        metric_name=name,
                        value=value,
                        unit=UNITS.get(name, "None"),
                    ))

                # Per-container stats
                client = get_docker()
                if client:
                    try:
                        # Get running awsclone containers
                        result = await db.execute(
                            select(Instance).where(
                                Instance.state == "running",
                                Instance.docker_container_id.isnot(None),
                            )
                        )
                        instances = result.scalars().all()

                        for inst in instances:
                            try:
                                container = client.containers.get(inst.docker_container_id)
                                stats = container.stats(stream=False)

                                cpu_delta = (
                                    stats["cpu_stats"]["cpu_usage"]["total_usage"]
                                    - stats["precpu_stats"]["cpu_usage"]["total_usage"]
                                )
                                system_delta = (
                                    stats["cpu_stats"]["system_cpu_usage"]
                                    - stats["precpu_stats"]["system_cpu_usage"]
                                )
                                cpu_pct = (cpu_delta / system_delta) * 100.0 if system_delta > 0 else 0

                                mem_used = stats["memory_stats"].get("usage", 0) / (1024 * 1024)

                                db.add(Metric(
                                    timestamp=now,
                                    metric_name="container_cpu_percent",
                                    value=round(cpu_pct, 2),
                                    unit="Percent",
                                    dimensions=json.dumps({"instance_id": inst.id}),
                                ))
                                db.add(Metric(
                                    timestamp=now,
                                    metric_name="container_memory_mb",
                                    value=round(mem_used, 2),
                                    unit="Megabytes",
                                    dimensions=json.dumps({"instance_id": inst.id}),
                                ))
                            except Exception:
                                pass
                    except Exception:
                        pass

                await db.commit()
        except Exception:
            pass

        await asyncio.sleep(METRICS_COLLECT_INTERVAL)


async def alarm_evaluator():
    """Background task: evaluate alarms every 60 seconds."""
    while True:
        try:
            async with async_session() as db:
                result = await db.execute(select(Alarm))
                alarms = result.scalars().all()

                for alarm in alarms:
                    cutoff = datetime.utcnow() - timedelta(seconds=alarm.period_seconds)
                    result = await db.execute(
                        text(
                            "SELECT AVG(value) FROM metrics "
                            "WHERE metric_name = :name AND timestamp > :cutoff"
                        ),
                        {"name": alarm.metric_name, "cutoff": cutoff},
                    )
                    row = result.first()
                    avg_value = row[0] if row and row[0] is not None else None

                    if avg_value is None:
                        alarm.state = "INSUFFICIENT_DATA"
                    else:
                        triggered = False
                        if alarm.comparison == "gt" and avg_value > alarm.threshold:
                            triggered = True
                        elif alarm.comparison == "lt" and avg_value < alarm.threshold:
                            triggered = True
                        elif alarm.comparison == "gte" and avg_value >= alarm.threshold:
                            triggered = True
                        elif alarm.comparison == "lte" and avg_value <= alarm.threshold:
                            triggered = True

                        alarm.state = "ALARM" if triggered else "OK"

                await db.commit()
        except Exception:
            pass

        await asyncio.sleep(60)


async def metrics_cleanup():
    """Background task: delete old metrics every hour."""
    while True:
        try:
            async with async_session() as db:
                cutoff = datetime.utcnow() - timedelta(days=METRICS_RETENTION_DAYS)
                await db.execute(
                    delete(Metric).where(Metric.timestamp < cutoff)
                )
                await db.commit()
        except Exception:
            pass

        await asyncio.sleep(3600)


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/metrics")
async def query_metrics(
    name: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    interval: int = 60,
    instance_id: Optional[str] = None,
    user: User = Depends(require_permission("cloudwatch:GetMetrics")),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    start_dt = datetime.fromisoformat(start) if start else now - timedelta(hours=1)
    end_dt = datetime.fromisoformat(end) if end else now

    # Build query with time bucketing
    if instance_id:
        result = await db.execute(
            text(
                "SELECT "
                "  strftime('%Y-%m-%dT%H:%M', timestamp, 'start of minute', "
                "    printf('-%d minutes', CAST(strftime('%M', timestamp) AS INTEGER) % :interval)) AS bucket, "
                "  AVG(value) AS value "
                "FROM metrics "
                "WHERE metric_name = :name AND timestamp BETWEEN :start AND :end "
                "  AND dimensions LIKE :dim_filter "
                "GROUP BY bucket ORDER BY bucket"
            ),
            {
                "name": name,
                "start": start_dt,
                "end": end_dt,
                "interval": max(1, interval // 60),
                "dim_filter": f'%"instance_id": "{instance_id}"%',
            },
        )
    else:
        result = await db.execute(
            text(
                "SELECT "
                "  strftime('%Y-%m-%dT%H:%M', timestamp, 'start of minute', "
                "    printf('-%d minutes', CAST(strftime('%M', timestamp) AS INTEGER) % :interval)) AS bucket, "
                "  AVG(value) AS value "
                "FROM metrics "
                "WHERE metric_name = :name AND timestamp BETWEEN :start AND :end "
                "  AND dimensions = '{}' "
                "GROUP BY bucket ORDER BY bucket"
            ),
            {
                "name": name,
                "start": start_dt,
                "end": end_dt,
                "interval": max(1, interval // 60),
            },
        )

    rows = result.fetchall()
    return {
        "metric_name": name,
        "datapoints": [
            {"timestamp": row[0], "value": round(row[1], 2)}
            for row in rows
        ],
        "unit": UNITS.get(name, "None"),
    }


@router.get("/metrics/latest")
async def get_latest_metrics(
    user: User = Depends(require_permission("cloudwatch:GetMetrics")),
    db: AsyncSession = Depends(get_db),
):
    latest = {}
    for name in ["cpu_percent", "memory_percent", "disk_percent"]:
        result = await db.execute(
            text(
                "SELECT value FROM metrics "
                "WHERE metric_name = :name AND dimensions = '{}' "
                "ORDER BY timestamp DESC LIMIT 1"
            ),
            {"name": name},
        )
        row = result.first()
        latest[name] = round(row[0], 1) if row else None

    return latest


@router.get("/metrics/instances/{instance_id}")
async def get_instance_metrics(
    instance_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    user: User = Depends(require_permission("cloudwatch:GetMetrics")),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    start_dt = datetime.fromisoformat(start) if start else now - timedelta(hours=1)
    end_dt = datetime.fromisoformat(end) if end else now

    result_data = {}
    for name in ["container_cpu_percent", "container_memory_mb"]:
        result = await db.execute(
            text(
                "SELECT timestamp, value FROM metrics "
                "WHERE metric_name = :name AND timestamp BETWEEN :start AND :end "
                "  AND dimensions LIKE :dim_filter "
                "ORDER BY timestamp"
            ),
            {
                "name": name,
                "start": start_dt,
                "end": end_dt,
                "dim_filter": f'%"instance_id": "{instance_id}"%',
            },
        )
        rows = result.fetchall()
        result_data[name] = {
            "datapoints": [
                {"timestamp": str(row[0]), "value": round(row[1], 2)}
                for row in rows
            ],
            "unit": UNITS.get(name, "None"),
        }

    return result_data


@router.get("/dashboard")
async def get_dashboard(
    user: User = Depends(require_permission("cloudwatch:GetMetrics")),
    db: AsyncSession = Depends(get_db),
):
    # Latest system metrics
    latest = {}
    for name in ["cpu_percent", "memory_percent", "disk_percent", "memory_used_mb", "disk_used_gb"]:
        result = await db.execute(
            text(
                "SELECT value FROM metrics "
                "WHERE metric_name = :name AND dimensions = '{}' "
                "ORDER BY timestamp DESC LIMIT 1"
            ),
            {"name": name},
        )
        row = result.first()
        latest[name] = round(row[0], 1) if row else None

    # Alarm states
    result = await db.execute(select(Alarm))
    alarms = result.scalars().all()
    alarm_summary = {
        "total": len(alarms),
        "ok": sum(1 for a in alarms if a.state == "OK"),
        "alarm": sum(1 for a in alarms if a.state == "ALARM"),
    }

    return {
        "metrics": latest,
        "alarms": alarm_summary,
        "uptime_seconds": int(psutil.boot_time()),
        "uptime_human": _format_uptime(),
    }


# ── Alarm CRUD ───────────────────────────────────────────────────────────

@router.get("/alarms")
async def list_alarms(
    user: User = Depends(require_permission("cloudwatch:DescribeAlarms")),
    db: AsyncSession = Depends(get_db),
):
    if user.is_root:
        result = await db.execute(select(Alarm))
    else:
        result = await db.execute(select(Alarm).where(Alarm.owner_id == user.id))
    alarms = result.scalars().all()
    return [_alarm_to_dict(a) for a in alarms]


@router.post("/alarms")
async def create_alarm(
    body: CreateAlarmRequest,
    user: User = Depends(require_permission("cloudwatch:CreateAlarm")),
    db: AsyncSession = Depends(get_db),
):
    if body.comparison not in ("gt", "lt", "gte", "lte"):
        raise HTTPException(status_code=400, detail="comparison must be gt, lt, gte, or lte")

    alarm = Alarm(
        id=generate_id(),
        owner_id=user.id,
        name=body.name,
        metric_name=body.metric_name,
        comparison=body.comparison,
        threshold=body.threshold,
        period_seconds=body.period_seconds,
    )
    db.add(alarm)
    await db.flush()
    return _alarm_to_dict(alarm)


@router.put("/alarms/{alarm_id}")
async def update_alarm(
    alarm_id: str,
    body: UpdateAlarmRequest,
    user: User = Depends(require_permission("cloudwatch:UpdateAlarm")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Alarm).where(Alarm.id == alarm_id))
    alarm = result.scalar_one_or_none()
    if not alarm:
        raise HTTPException(status_code=404, detail="Alarm not found")

    if body.name is not None:
        alarm.name = body.name
    if body.metric_name is not None:
        alarm.metric_name = body.metric_name
    if body.comparison is not None:
        if body.comparison not in ("gt", "lt", "gte", "lte"):
            raise HTTPException(status_code=400, detail="comparison must be gt, lt, gte, or lte")
        alarm.comparison = body.comparison
    if body.threshold is not None:
        alarm.threshold = body.threshold
    if body.period_seconds is not None:
        alarm.period_seconds = body.period_seconds

    await db.flush()
    return _alarm_to_dict(alarm)


@router.delete("/alarms/{alarm_id}")
async def delete_alarm(
    alarm_id: str,
    user: User = Depends(require_permission("cloudwatch:DeleteAlarm")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Alarm).where(Alarm.id == alarm_id))
    alarm = result.scalar_one_or_none()
    if not alarm:
        raise HTTPException(status_code=404, detail="Alarm not found")

    await db.delete(alarm)
    return {"detail": "Alarm deleted"}


# ── Helpers ──────────────────────────────────────────────────────────────

def _alarm_to_dict(alarm: Alarm) -> dict:
    return {
        "id": alarm.id,
        "owner_id": alarm.owner_id,
        "name": alarm.name,
        "metric_name": alarm.metric_name,
        "comparison": alarm.comparison,
        "threshold": alarm.threshold,
        "period_seconds": alarm.period_seconds,
        "state": alarm.state,
        "created_at": alarm.created_at.isoformat(),
    }


def _format_uptime() -> str:
    import time
    uptime = time.time() - psutil.boot_time()
    days = int(uptime // 86400)
    hours = int((uptime % 86400) // 3600)
    mins = int((uptime % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    return " ".join(parts)
