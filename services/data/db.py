"""PostgreSQL access for the candle ingestion worker."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

import asyncpg

_pool: asyncpg.Pool | None = None


@dataclass(slots=True)
class CandleRow:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    dsn = os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


UPSERT_SQL = """
INSERT INTO "Candle" (
    "id", "symbol", "timeframe", "open", "high", "low", "close", "volume", "timestamp"
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT ("symbol", "timeframe", "timestamp") DO UPDATE SET
    "open"   = EXCLUDED."open",
    "high"   = EXCLUDED."high",
    "low"    = EXCLUDED."low",
    "close"  = EXCLUDED."close",
    "volume" = EXCLUDED."volume"
"""


async def upsert_candles(rows: Iterable[CandleRow]) -> int:
    pool = await init_pool()
    payload = [
        (
            uuid.uuid4().hex,
            r.symbol,
            r.timeframe,
            r.open,
            r.high,
            r.low,
            r.close,
            r.volume,
            r.timestamp,
        )
        for r in rows
    ]
    if not payload:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(UPSERT_SQL, payload)
    return len(payload)
