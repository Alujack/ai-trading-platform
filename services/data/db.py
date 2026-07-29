"""PostgreSQL access for the candle ingestion worker."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

import asyncpg

log = logging.getLogger("data.db")

# ~60s of cover for a cold Postgres start (compose brings both up together).
DB_CONNECT_ATTEMPTS = 30
DB_CONNECT_BACKOFF_SECONDS = 2.0

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
    """Open the shared pool, retrying while Postgres is still coming up.

    compose's `depends_on: service_healthy` is not sufficient: pg_isready can
    report healthy while the server is still in recovery, and connecting then
    raises CannotConnectNowError. This used to kill the worker at boot — and
    because the container runs under `watchfiles` (PID 1, which only reruns the
    child on a *.py change, never on a crash) the container stayed "Up" with a
    dead worker and `restart: unless-stopped` never fired. Silent, and it cost
    18 days of ingestion on 2026-07-10. Retry instead of dying.
    """
    global _pool
    if _pool is not None:
        return _pool
    dsn = os.environ["DATABASE_URL"]
    last: Exception | None = None
    for attempt in range(1, DB_CONNECT_ATTEMPTS + 1):
        try:
            _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
            return _pool
        except (asyncpg.CannotConnectNowError, OSError) as exc:
            # CannotConnectNowError = server starting up; OSError = not yet
            # listening. Both are transient on a cold `docker compose up`.
            last = exc
            log.warning(
                "db_connect_retry attempt=%d/%d err=%s", attempt, DB_CONNECT_ATTEMPTS, exc
            )
            await asyncio.sleep(DB_CONNECT_BACKOFF_SECONDS)
    raise RuntimeError(
        f"could not connect to Postgres after {DB_CONNECT_ATTEMPTS} attempts"
    ) from last


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
