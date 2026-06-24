"""Page Twelve Data backward to build deep history for backtesting.

Usage:
    python backfill_history.py --symbol XAUUSD --timeframe 60min --target 8000

Twelve Data returns up to 5000 bars per call on every plan (incl. free) — at the
same 1-credit cost as a small request. This script pulls 5000-bar pages, walking
backward with end_date until the DB holds at least `target` bars (or the provider
runs out of older history). Existing rows are upserted, so re-running is safe.

Does NOT compute indicators — run `indicator_calculator.py --full` afterward.
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

# Bars per request. 5000 is the free-tier max and costs the same one credit as a
# tiny request, so always pull the full page.
PAGE_SIZE = 5000


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
    prev_oldest: datetime | None = None
    async with httpx.AsyncClient() as client:
        while True:
            count, oldest = await _current_state(pool, symbol, timeframe)
            log.info("state symbol=%s tf=%s count=%d oldest=%s", symbol, timeframe, count, oldest)
            if count >= target:
                log.info("target reached (%d >= %d), stopping", count, target)
                return
            # Stall guard: if the oldest bar didn't move back after a page, the
            # provider has no more history for this timeframe — stop.
            if oldest is not None and prev_oldest is not None and oldest >= prev_oldest:
                log.info("oldest bar did not recede (%s) — provider history exhausted, stopping", oldest)
                return
            prev_oldest = oldest
            if oldest is None:
                # No data yet — call without end_date to get the most recent batch.
                end = None
            else:
                end = oldest - step
            rows = await fetch_candles(client, symbol, timeframe, end_date=end, output_size=PAGE_SIZE)
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
