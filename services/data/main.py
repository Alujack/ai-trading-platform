"""Candle ingestion worker.

Runs one scheduled loop per timeframe, each at a cadence matched to that
timeframe. Sized for Twelve Data's free tier (8 req/min, 800 req/day) — see
TIMEFRAME_PERIOD_SECONDS below for the trade-off.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from db import close_pool, init_pool, upsert_candles
from fetcher import SYMBOL_MAP, fetch_candles
from indicator_calculator import calculate_indicators
from strategy_runner import run_once as run_strategy_scan

# News ingestion moved to the n8n automation layer (see docs/plans/06-…). The
# worker is back to a single job: candles → indicators → strategies.

SYMBOLS = list(SYMBOL_MAP.keys())  # XAUUSD, EURUSD, BTCUSD

# Each TF refreshes at a period that's a small multiple of the TF itself.
# Total fetch rate: 60 req/hr (3 symbols * 5 TFs / their periods), so we stay
# inside the per-minute limit but exceed the 800/day cap after ~13 hours of
# continuous running on free tier.
TIMEFRAME_PERIOD_SECONDS: dict[str, int] = {
    # 60s 1min ingestion when the broker feed is on (free + real-time); the
    # conservative 5-min period otherwise keeps TwelveData under its daily cap.
    "1min": 60 if os.environ.get("CANDLE_SOURCE", "").strip().lower() == "mt5" else 5 * 60,
    "5min": 15 * 60,
    "15min": 30 * 60,
    "60min": 60 * 60,
    "daily": 60 * 60,
}

# Strategy runner cadence (matches the retired TS signal cron's 15-min tick).
# Override via env for scalp timeframes: a 1min strategy needs ~60s scans, which
# is only viable when CANDLE_SOURCE=mt5 (no HTTP-provider rate limits).
STRATEGY_PERIOD_SECONDS = int(os.environ.get("STRATEGY_PERIOD_SECONDS", 15 * 60))

# Where to ping the API so it can push realtime updates to the dashboard (SSE).
_API_BASE = os.environ.get("API_PUBLIC_URL", "http://localhost:8000").rstrip("/")
RT_NOTIFY_URL = f"{_API_BASE}/api/internal/rt-notify"


async def _notify_rt(client: httpx.AsyncClient, type_: str, symbol: str, timeframe: str) -> None:
    """Best-effort realtime ping; never let it disturb the ingest loop."""
    try:
        await client.post(
            RT_NOTIFY_URL,
            json={"type": type_, "symbol": symbol, "timeframe": timeframe},
            timeout=5.0,
        )
    except Exception:  # noqa: BLE001 — realtime is a nicety, not load-bearing
        pass

log = logging.getLogger("data.worker")


def _configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "info").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    logging.Formatter.converter = lambda *_: datetime.now(timezone.utc).timetuple()
    # httpx logs request URLs (incl. ?apikey=...) at INFO — never leak the key.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _fetch_and_store(
    client: httpx.AsyncClient, symbol: str, timeframe: str
) -> None:
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        rows = await fetch_candles(client, symbol, timeframe)
    except Exception as exc:  # noqa: BLE001 — log and continue, do not kill the loop
        log.error("fetch_failed at=%s symbol=%s tf=%s err=%s", fetched_at, symbol, timeframe, exc)
        return
    written = await upsert_candles(rows)
    log.info(
        "fetched at=%s symbol=%s tf=%s candles=%d upserts=%d",
        fetched_at,
        symbol,
        timeframe,
        len(rows),
        written,
    )
    if written > 0:
        try:
            await calculate_indicators(symbol, timeframe)
        except Exception as exc:  # noqa: BLE001 — indicators are derived, don't kill the loop
            log.error(
                "indicators_failed symbol=%s tf=%s err=%s", symbol, timeframe, exc
            )
        await _notify_rt(client, "candle", symbol, timeframe)


async def _scheduled_loop(timeframe: str, client: httpx.AsyncClient, period_seconds: int) -> None:
    log.info("loop_start tf=%s period_s=%d", timeframe, period_seconds)
    while True:
        started = asyncio.get_event_loop().time()
        for symbol in SYMBOLS:
            await _fetch_and_store(client, symbol, timeframe)
        elapsed = asyncio.get_event_loop().time() - started
        await asyncio.sleep(max(0.0, period_seconds - elapsed))


async def _periodic_loop(
    name: str,
    task: "Callable[[httpx.AsyncClient], Awaitable[int]]",
    client: httpx.AsyncClient,
    period_seconds: int,
) -> None:
    log.info("loop_start name=%s period_s=%d", name, period_seconds)
    while True:
        started = asyncio.get_event_loop().time()
        try:
            await task(client)
        except Exception as exc:  # noqa: BLE001 — log and continue, do not kill the loop
            log.error("loop_task_failed name=%s err=%s", name, exc)
        elapsed = asyncio.get_event_loop().time() - started
        await asyncio.sleep(max(0.0, period_seconds - elapsed))


async def main() -> None:
    load_dotenv()
    _configure_logging()
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    if "TWELVEDATA_API_KEY" not in os.environ:
        raise RuntimeError("TWELVEDATA_API_KEY is not set in environment")

    await init_pool()
    log.info("worker_starting symbols=%s timeframes=%s", SYMBOLS, list(TIMEFRAME_PERIOD_SECONDS))

    async with httpx.AsyncClient() as client:
        try:
            await asyncio.gather(
                *(
                    _scheduled_loop(tf, client, period)
                    for tf, period in TIMEFRAME_PERIOD_SECONDS.items()
                ),
                _periodic_loop("strategy_runner", run_strategy_scan, client, STRATEGY_PERIOD_SECONDS),
            )
        finally:
            await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
