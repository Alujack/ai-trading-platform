"""Candles + merged indicators — port of `routes/candles.routes.ts`."""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from ...core.serialization import iso, ser
from ...db.models import Candle, Indicator
from ..dependencies import Db, Timeframe

router = APIRouter(tags=["market"])


@router.get("/api/candles")
async def list_candles(
    session: Db,
    symbol: str = Query(min_length=1, max_length=20),
    timeframe: Timeframe = Query(...),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    """Newest-first candles with each bar's indicator snapshot attached (or null)."""
    candles = (
        (
            await session.execute(
                select(Candle)
                .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
                .order_by(Candle.timestamp.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    if not candles:
        return []

    indicators = (
        (
            await session.execute(
                select(Indicator).where(
                    Indicator.symbol == symbol,
                    Indicator.timeframe == timeframe,
                    Indicator.timestamp.in_([c.timestamp for c in candles]),
                )
            )
        )
        .scalars()
        .all()
    )
    by_ts = {iso(i.timestamp): i for i in indicators}

    merged = []
    for c in candles:
        ind = by_ts.get(iso(c.timestamp))
        merged.append(
            {
                "id": c.id,
                "symbol": c.symbol,
                "timeframe": c.timeframe,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "timestamp": c.timestamp,
                "createdAt": c.createdAt,
                "indicators": (
                    {
                        "rsi": ind.rsi,
                        "ema20": ind.ema20,
                        "ema50": ind.ema50,
                        "ema200": ind.ema200,
                        "atr": ind.atr,
                    }
                    if ind is not None
                    else None
                ),
            }
        )

    return ser(merged)
