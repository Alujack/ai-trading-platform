"""Performance metrics — port of `routes/performance.routes.ts`."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from ...core.serialization import num_or_none
from ...db.enums import TradeStatus
from ...db.models import Trade
from ...domain.performance.metrics import TradeStats, compute_performance
from ..dependencies import Db

router = APIRouter(tags=["trading"])


@router.get("/api/performance")
async def get_performance(session: Db) -> dict[str, Any]:
    trades = (
        (
            await session.execute(
                select(Trade)
                .where(Trade.status == TradeStatus.CLOSED)
                .order_by(Trade.closedAt.asc())
            )
        )
        .scalars()
        .all()
    )

    stats = [
        TradeStats(
            entryPrice=float(t.entryPrice),
            exitPrice=num_or_none(t.exitPrice),
            profitLoss=num_or_none(t.profitLoss),
            direction=t.signal.direction.value,
            stopLoss=float(t.signal.stopLoss),
        )
        for t in trades
    ]
    return compute_performance(stats)
