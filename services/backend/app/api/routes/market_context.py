"""Market-context briefing — port of `routes/marketContext.routes.ts`."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...domain.market_context import get_market_context
from ..dependencies import Db, Timeframe

router = APIRouter(tags=["ai"])


@router.get("/api/market-context")
async def market_context(
    session: Db,
    symbol: str = Query(min_length=1, max_length=20),
    timeframe: Timeframe = "60min",
) -> dict[str, Any]:
    return await get_market_context(session, symbol, timeframe)
