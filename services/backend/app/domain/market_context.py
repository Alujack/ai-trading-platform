"""Market-context builder — port of `apps/api/src/services/marketContext.ts`.

Assembles recent candles + indicators + upcoming news for a (symbol, timeframe)
and asks the AI layer for a structured briefing (bias, key levels, risks).
Shared by `GET /api/market-context` and the 06:00 UTC daily briefing, with a
Redis cache so both paths reuse one AI call.

Phase 3 change: the AI call is now in-process (`integrations.ai.client`) instead
of an HTTP hop to `services/ai`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import HttpError
from ..core.serialization import iso, num_or_none
from ..db.models import Candle, Indicator, NewsEvent
from ..db.redis_client import cache_get, cache_set
from ..integrations.ai import client as ai
from ..integrations.ai.schemas import MarketContextRequest
from ..jobs.clock import utcnow

log = logging.getLogger("backend.market-context")

CACHE_TTL_SECONDS = 10 * 60
CANDLE_LOOKBACK = 50
NEWS_LOOKAHEAD = 8


def _cache_key(symbol: str, timeframe: str) -> str:
    return f"market-context:{symbol}:{timeframe}"


async def _read_cache(key: str) -> dict[str, Any] | None:
    raw = await cache_get(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    payload["cached"] = True
    return payload


async def get_market_context(
    session: AsyncSession, symbol: str, timeframe: str
) -> dict[str, Any]:
    """Build (or reuse from cache) the market-context briefing for one series."""
    key = _cache_key(symbol, timeframe)
    cached = await _read_cache(key)
    if cached:
        return cached

    candles = (
        (
            await session.execute(
                select(Candle)
                .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
                .order_by(Candle.timestamp.desc())
                .limit(CANDLE_LOOKBACK)
            )
        )
        .scalars()
        .all()
    )
    if not candles:
        raise HttpError(404, f"No candles for {symbol}/{timeframe}")

    timestamps = [c.timestamp for c in candles]
    indicators = (
        (
            await session.execute(
                select(Indicator)
                .where(
                    Indicator.symbol == symbol,
                    Indicator.timeframe == timeframe,
                    Indicator.timestamp.in_(timestamps),
                )
                .order_by(Indicator.timestamp.desc())
            )
        )
        .scalars()
        .all()
    )

    upcoming_news = (
        (
            await session.execute(
                select(NewsEvent)
                .where(NewsEvent.scheduledAt > utcnow().replace(tzinfo=None))
                .order_by(NewsEvent.scheduledAt.asc())
                .limit(NEWS_LOOKAHEAD)
            )
        )
        .scalars()
        .all()
    )

    request = MarketContextRequest.model_validate(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [
                {
                    "timestamp": iso(c.timestamp),
                    "open": num_or_none(c.open),
                    "high": num_or_none(c.high),
                    "low": num_or_none(c.low),
                    "close": num_or_none(c.close),
                    "volume": num_or_none(c.volume),
                }
                for c in candles
            ],
            "indicators": [
                {
                    "timestamp": iso(i.timestamp),
                    "rsi": num_or_none(i.rsi),
                    "ema20": num_or_none(i.ema20),
                    "ema50": num_or_none(i.ema50),
                    "ema200": num_or_none(i.ema200),
                    "atr": num_or_none(i.atr),
                }
                for i in indicators
            ],
            "news": [
                {
                    "title": n.title,
                    "impact": n.impact.value,
                    "currency": n.currency,
                    "scheduledAt": iso(n.scheduledAt),
                }
                for n in upcoming_news
            ],
        }
    )

    try:
        result = await ai.market_context(request)
    except HttpError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Fail closed with the same 503 the Express proxy produced when the AI
        # hop was unavailable, so the dashboard's error copy still applies.
        raise HttpError(503, f"AI analysis unavailable: {exc}") from exc

    payload: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "bias": result.bias,
        "summary": result.summary,
        "keyLevels": result.keyLevels or [],
        "risks": result.risks or [],
        "generatedAt": iso(utcnow()),
        "cached": False,
    }
    await cache_set(key, json.dumps(payload), CACHE_TTL_SECONDS)
    return payload
