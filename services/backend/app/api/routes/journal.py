"""Trade journal — port of `routes/journal.routes.ts`.

Notes + AI review per trade, with the trade/signal context the dashboard needs
to render each entry.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from ...core.serialization import iso, num_or_none
from ...db.models import Journal
from ..dependencies import Db

router = APIRouter(tags=["trading"])


@router.get("/api/journal")
async def list_journal(
    session: Db, limit: int = Query(default=30, ge=1, le=100)
) -> dict[str, Any]:
    entries = (
        (
            await session.execute(
                select(Journal).order_by(Journal.createdAt.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )

    data = [
        {
            "id": e.id,
            "notes": e.notes,
            "aiReview": e.aiReview,
            "emotions": e.emotions,
            "createdAt": iso(e.createdAt),
            "symbol": e.trade.signal.symbol,
            "direction": e.trade.signal.direction.value,
            "strategyName": e.trade.signal.strategyName,
            "status": e.trade.status.value,
            "profitLoss": num_or_none(e.trade.profitLoss),
            "closedAt": None if e.trade.closedAt is None else iso(e.trade.closedAt),
        }
        for e in entries
    ]
    return {"data": data, "count": len(data)}
