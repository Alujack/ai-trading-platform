"""Page Twelve Data backward to build up enough history for EMA200 etc.

Usage:
    python backfill_history.py --symbol XAUUSD --timeframe 60min --target 500

Twelve Data returns up to 100 bars per call (free tier outputsize cap). This
script pages back using end_date until the DB has at least `target` bars for
the (symbol, timeframe). Existing rows are upserted so re-running is safe.

Does NOT compute indicators — run indicator_calculator.py afterward.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv

from db import close_pool, init_pool, upsert_candles
from fetcher import fetch_candles
from indicator_calculator import normalize_timeframe

log = logging.getLogger("data.backfill")

# Step back by one interval from the oldest known bar so we don't refetch it.
_TIMEFRAME_STEP: dict[str, timedelta] = {
    "1min": timedelta(minutes=1),
    "5min": timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "60min": timedelta(hours=1),
    "daily": timedelta(days=1),
}

# Pause between API calls — Twelve Data free tier is 8 req/min.
PER_CALL_SLEEP_SECONDS = 8.0


async def _current_state(pool, symbol: str, timeframe: str) -> tuple[int, datetime | None]:
    row = await pool.fetchrow(
        'SELECT COUNT(*) AS n, MIN("timestamp") AS oldest FROM "Candle" '
        'WHERE "symbol" = $1 AND "timeframe" = $2',
        symbol,
        timeframe,
    )
    return row["n"], row["oldest"]


async def backfill(symbol: str, timeframe: str, target: int) -> None:
    timeframe = normalize_timeframe(timeframe)
    if timeframe not in _TIMEFRAME_STEP:
        raise ValueError(f"No step defined for timeframe {timeframe}")
    step = _TIMEFRAME_STEP[timeframe]

    pool = await init_pool()
    async with httpx.AsyncClient() as client:
        while True:
            count, oldest = await _current_state(pool, symbol, timeframe)
            log.info("state symbol=%s tf=%s count=%d oldest=%s", symbol, timeframe, count, oldest)
            if count >= target:
                log.info("target reached (%d >= %d), stopping", count, target)
                return
            if oldest is None:
                # No data yet — call without end_date to get the most recent batch.
                end = None
            else:
                end = oldest - step
            rows = await fetch_candles(client, symbol, timeframe, end_date=end)
            if not rows:
                log.warning("provider returned 0 rows (end=%s) — stopping", end)
                return
            written = await upsert_candles(rows)
            log.info("page fetched=%d upserted=%d end_date=%s", len(rows), written, end)
            await asyncio.sleep(PER_CALL_SLEEP_SECONDS)


async def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--target", type=int, default=500)
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    for var in ("DATABASE_URL", "TWELVEDATA_API_KEY"):
        if var not in os.environ:
            raise RuntimeError(f"{var} is not set in environment")

    try:
        await backfill(args.symbol, args.timeframe, args.target)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())
