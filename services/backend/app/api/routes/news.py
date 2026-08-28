"""Economic calendar + AI-summarized news — port of `routes/news.routes.ts`."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from ...core.serialization import iso
from ...db.enums import Impact
from ...db.models import NewsEvent
from ...jobs.clock import naive_utcnow
from ..dependencies import Db, ImpactParam

router = APIRouter(tags=["market"])


@router.get("/api/news")
async def list_news(
    session: Db,
    limit: int = Query(default=25, ge=1, le=100),
    impact: ImpactParam | None = None,
) -> dict[str, Any]:
    filters = [NewsEvent.impact == Impact(impact)] if impact else []
    rows = (
        (
            await session.execute(
                select(NewsEvent)
                .where(*filters)
                .order_by(NewsEvent.scheduledAt.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    now = naive_utcnow()
    data = [
        {
            "id": r.id,
            "title": r.title,
            "impact": r.impact.value,
            "currency": r.currency,
            "scheduledAt": iso(r.scheduledAt),
            "actual": r.actual,
            "forecast": r.forecast,
            "previous": r.previous,
            "aiSummary": r.aiSummary,
            "upcoming": r.scheduledAt > now,
        }
        for r in rows
    ]
    return {"data": data, "count": len(data)}
