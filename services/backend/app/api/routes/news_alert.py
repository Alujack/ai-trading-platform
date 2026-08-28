"""HIGH-impact news alerts from n8n — port of `routes/newsAlert.routes.ts`.

The risk engine already enforces the ±30-min blackout off the `NewsEvent` rows;
this Redis key is a fast, proactive signal the dashboard/API can read without a
DB scan.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response

from ...core.logging import get_logger
from ...core.serialization import iso
from ...db.redis_client import cache_get, cache_set
from ...jobs.clock import utcnow

log = get_logger("backend.news-alert")
router = APIRouter(tags=["internal"])

# How long a HIGH-impact alert stays "active" in the cache.
ALERT_TTL_SECONDS = 90 * 60
ALERT_KEY = "news:high-impact:active"


def _as_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@router.post("/api/internal/news-alert")
async def post_news_alert(request: Request, response: Response) -> dict[str, Any]:
    """Receive a HIGH-impact calendar alert from n8n (Workflow A's optional branch).

    Idempotent: writes/refreshes a single Redis key the UI can poll, so the system
    reacts immediately instead of waiting for the next signal cycle.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    title = _as_string(body.get("title"))
    currency = _as_string(body.get("currency"))
    scheduled_at = _as_string(body.get("scheduledAt"))

    if not title or not scheduled_at:
        response.status_code = 400
        return {"error": "title and scheduledAt are required"}

    payload = json.dumps(
        {
            "title": title,
            "currency": currency or "UNKNOWN",
            "scheduledAt": scheduled_at,
            "receivedAt": iso(utcnow()),
        }
    )
    await cache_set(ALERT_KEY, payload, ALERT_TTL_SECONDS)
    log.info(
        '[news-alert] HIGH impact %s "%s" @ %s', currency or "?", title, scheduled_at
    )

    response.status_code = 202
    return {"ok": True}


@router.get("/api/internal/news-alert")
async def get_news_alert() -> dict[str, Any]:
    """Lets the dashboard surface a banner without hitting Postgres."""
    raw = await cache_get(ALERT_KEY)
    active: Any = None
    if raw:
        try:
            active = json.loads(raw)
        except json.JSONDecodeError:
            log.error("[news-alert] cached alert was not valid JSON")
    return {"active": active}
