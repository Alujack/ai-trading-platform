"""Health and readiness — ports `/health` and adds the Kubernetes-style probes.

`GET /api/health` keeps the Express body and status semantics exactly (200 when
both dependencies answer, 503 with `"degraded"` otherwise) because the dashboard
reads it. `/health/live` and `/health/ready` are new, per plan Phase 1.
"""
from __future__ import annotations

from fastapi import APIRouter, Response

from ...db.redis_client import ping_redis
from ...db.session import ping_db
from ...jobs.scheduler import scheduler_state

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def api_health(response: Response) -> dict[str, str]:
    """Dependency check with the legacy body: `{status, db, redis}`."""
    db_ok = await ping_db()
    redis_ok = await ping_redis()
    healthy = db_ok and redis_ok
    response.status_code = 200 if healthy else 503
    return {
        "status": "ok" if healthy else "degraded",
        "db": "connected" if db_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected",
    }


@router.get("/health")
async def root_health() -> dict[str, str]:
    """Liveness only — matches the Express `GET /health` shape."""
    return {"status": "ok"}


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Process is up. Deliberately touches no dependency."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, object]:
    """Ready to serve: Postgres and Redis both answer. Reports job ownership too."""
    db_ok = await ping_db()
    redis_ok = await ping_redis()
    healthy = db_ok and redis_ok
    response.status_code = 200 if healthy else 503
    return {
        "status": "ready" if healthy else "not_ready",
        "db": "connected" if db_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected",
        "jobs": scheduler_state(),
    }
