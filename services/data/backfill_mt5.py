"""Page the MT5 bridge backward by date to build deep, feed-consistent history.

Why this exists alongside `backfill_history.py` (which pages TwelveData):

* **Same feed as live.** We execute on Exness via the MT5 bridge, so training and
  backtesting on Exness bars removes a whole class of silent train/live drift.
  TwelveData is a different venue with different ticks, spreads and session edges.
* **No API quota.** TwelveData's free tier is 8 req/min / 800 per day, which the
  live worker already consumes; a deep 1min backfill cannot coexist with it.

Requires the bridge's `/candles_range` endpoint (added for this) — the older
`/candles` is capped at 5000 bars from *now*, i.e. ~3.4 days of M1.

Observed Exness-MT5Trial6 depth for XAUUSDm (probe before trusting a target):
    15min -> 2022+      5min -> ~2025-06+      1min -> ~2026-05+

Usage:
    python backfill_mt5.py --symbol XAUUSD --timeframe 15min --start 2022-06-01
    python backfill_mt5.py --symbol XAUUSD --timeframe 1min  --start 2026-05-01

Idempotent: candle upserts are ON CONFLICT DO UPDATE, so re-running is safe.
Does NOT compute indicators — run `indicator_calculator.py --full` afterwards.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from dotenv import load_dotenv

from db import CandleRow, close_pool, init_pool, upsert_candles
from fetcher import _mt5_symbol  # broker Market Watch name (XAUUSD -> XAUUSDm)
from indicator_calculator import normalize_timeframe

log = logging.getLogger("data.backfill_mt5")

# Chunk sizes chosen so each request stays well under MT5's per-call limits while
# keeping the number of round trips sane.
_CHUNK_DAYS: dict[str, int] = {
    "1min": 3,
    "5min": 14,
    "15min": 45,
    "60min": 180,
    "daily": 3650,
}


async def fetch_range(
    client: httpx.AsyncClient, symbol: str, timeframe: str, start: datetime, end: datetime
) -> list[CandleRow]:
    base = os.environ.get("MT5_BRIDGE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("MT5_BRIDGE_URL is not set")
    resp = await client.get(
        f"{base}/candles_range/{_mt5_symbol(symbol)}",
        params={
            "timeframe": timeframe,
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
        },
        headers={"X-Bridge-Token": os.environ.get("MT5_BRIDGE_TOKEN", "")},
        timeout=120.0,
    )
    resp.raise_for_status()
    rows: list[CandleRow] = []
    for e in resp.json():
        # Bridge returns epoch seconds UTC; we store naive UTC to match the rest
        # of the Candle table (see fetcher._fetch_mt5).
        ts = datetime.fromtimestamp(int(e["timestamp"]), tz=timezone.utc).replace(tzinfo=None)
        rows.append(
            CandleRow(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=Decimal(str(e["open"])),
                high=Decimal(str(e["high"])),
                low=Decimal(str(e["low"])),
                close=Decimal(str(e["close"])),
                volume=Decimal(str(e.get("volume") or 0)),
            )
        )
    return rows


async def backfill(symbol: str, timeframe: str, start: datetime, end: datetime) -> int:
    chunk = timedelta(days=_CHUNK_DAYS.get(timeframe, 30))
    total = 0
    empty_streak = 0
    # Walk backward from `end` so we stop early once we run off the front of the
    # broker's history rather than grinding through years of empty ranges.
    cursor = end
    async with httpx.AsyncClient() as client:
        while cursor > start:
            lo = max(start, cursor - chunk)
            rows = await fetch_range(client, symbol, timeframe, lo, cursor)
            if rows:
                written = await upsert_candles(rows)
                total += written
                empty_streak = 0
                log.info(
                    "chunk %s..%s bars=%d written=%d total=%d",
                    lo.date(), cursor.date(), len(rows), written, total,
                )
            else:
                empty_streak += 1
                log.info("chunk %s..%s empty (streak=%d)", lo.date(), cursor.date(), empty_streak)
                # Weekends/holidays produce isolated empties; several in a row
                # means we've passed the start of available history.
                if empty_streak >= 4:
                    log.warning("stopping: %d consecutive empty chunks — end of history", empty_streak)
                    break
            cursor = lo
    return total


def _parse_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


async def main_async() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", required=True)
    p.add_argument("--start", required=True, help="ISO date, e.g. 2022-06-01")
    p.add_argument("--end", help="ISO date; defaults to now")
    args = p.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    tf = normalize_timeframe(args.timeframe)
    start = _parse_day(args.start)
    end = _parse_day(args.end) if args.end else datetime.now(tz=timezone.utc)

    await init_pool()
    try:
        total = await backfill(args.symbol, tf, start, end)
        log.info("done symbol=%s tf=%s rows_written=%d", args.symbol, tf, total)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main_async())
