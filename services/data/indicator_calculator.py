"""Compute RSI / EMA / ATR indicators from stored candles and upsert them.

Designed to be called after each candle ingestion cycle in main.py, and also
runnable directly from the command line for one-off recomputes:

    python indicator_calculator.py --symbol XAUUSD --timeframe 60min --show 5

Timeframe labels match what's stored on Candle.timeframe (e.g. "60min", not "1h").
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import uuid
from decimal import Decimal

import asyncpg
import pandas as pd
import pandas_ta_classic as ta
from dotenv import load_dotenv

from db import close_pool, init_pool

log = logging.getLogger("data.indicators")

# EMA 200 needs ~200 prior bars; pull extra so the recent window has warm values.
LOOKBACK_BARS = 400

# Accept "1h" / "1hour" as aliases for the DB-stored "60min".
_TIMEFRAME_ALIAS: dict[str, str] = {
    "1h": "60min",
    "1hour": "60min",
    "1d": "daily",
    "1day": "daily",
}


def normalize_timeframe(tf: str) -> str:
    return _TIMEFRAME_ALIAS.get(tf, tf)


async def _load_candles(
    pool: asyncpg.Pool, symbol: str, timeframe: str, lookback_bars: int = LOOKBACK_BARS
) -> pd.DataFrame:
    rows = await pool.fetch(
        """
        SELECT "timestamp", "open", "high", "low", "close", "volume"
        FROM "Candle"
        WHERE "symbol" = $1 AND "timeframe" = $2
        ORDER BY "timestamp" DESC
        LIMIT $3
        """,
        symbol,
        timeframe,
        lookback_bars,
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        [
            {
                "timestamp": r["timestamp"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            for r in rows
        ]
    )
    return df.sort_values("timestamp").reset_index(drop=True)


def _compute(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"timestamp": df["timestamp"]})
    out["rsi"] = ta.rsi(df["close"], length=14)
    out["ema20"] = ta.ema(df["close"], length=20)
    out["ema50"] = ta.ema(df["close"], length=50)
    out["ema200"] = ta.ema(df["close"], length=200)
    out["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    return out


UPSERT_SQL = """
INSERT INTO "Indicator" (
    "id", "symbol", "timeframe", "timestamp", "rsi", "ema20", "ema50", "ema200", "atr"
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT ("symbol", "timeframe", "timestamp") DO UPDATE SET
    "rsi"    = EXCLUDED."rsi",
    "ema20"  = EXCLUDED."ema20",
    "ema50"  = EXCLUDED."ema50",
    "ema200" = EXCLUDED."ema200",
    "atr"    = EXCLUDED."atr"
"""


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(str(value))


async def _upsert_indicators(
    pool: asyncpg.Pool, symbol: str, timeframe: str, df: pd.DataFrame
) -> int:
    payload: list[tuple] = []
    for _, row in df.iterrows():
        rsi = _to_decimal(row["rsi"])
        ema20 = _to_decimal(row["ema20"])
        ema50 = _to_decimal(row["ema50"])
        ema200 = _to_decimal(row["ema200"])
        atr = _to_decimal(row["atr"])
        # Skip leading rows where every indicator is still NaN (no useful info).
        if all(v is None for v in (rsi, ema20, ema50, ema200, atr)):
            continue
        payload.append(
            (
                uuid.uuid4().hex,
                symbol,
                timeframe,
                row["timestamp"],
                rsi,
                ema20,
                ema50,
                ema200,
                atr,
            )
        )
    if not payload:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(UPSERT_SQL, payload)
    return len(payload)


async def calculate_indicators(
    symbol: str, timeframe: str, lookback_bars: int = LOOKBACK_BARS
) -> int:
    """Compute indicators for (symbol, timeframe) and upsert into Indicator.

    Returns the number of rows written. Safe to call after each candle
    ingestion — the upsert keeps in-progress bars current. Pass a large
    lookback_bars (or use the CLI --full flag) to recompute across the entire
    stored history, which backtesting needs.
    """
    timeframe = normalize_timeframe(timeframe)
    pool = await init_pool()
    df = await _load_candles(pool, symbol, timeframe, lookback_bars)
    if df.empty:
        log.info("indicators_skip symbol=%s tf=%s reason=no_candles", symbol, timeframe)
        return 0
    indicators_df = _compute(df)
    written = await _upsert_indicators(pool, symbol, timeframe, indicators_df)
    log.info(
        "indicators_written symbol=%s tf=%s rows=%d candles=%d",
        symbol,
        timeframe,
        written,
        len(df),
    )
    return written


async def _fetch_recent_rows(
    pool: asyncpg.Pool, symbol: str, timeframe: str, limit: int
):
    return await pool.fetch(
        """
        SELECT "timestamp", "rsi", "ema20", "ema50", "ema200", "atr"
        FROM "Indicator"
        WHERE "symbol" = $1 AND "timeframe" = $2
        ORDER BY "timestamp" DESC
        LIMIT $3
        """,
        symbol,
        timeframe,
        limit,
    )


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.4f}"


async def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument(
        "--show", type=int, default=5, help="Print last N indicator rows after compute"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Recompute indicators across ALL stored candles (for backtesting), not just the recent window",
    )
    parser.add_argument(
        "--lookback", type=int, default=None,
        help="Explicit number of most-recent candles to compute over (overrides --full)",
    )
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")

    timeframe = normalize_timeframe(args.timeframe)
    lookback = args.lookback if args.lookback is not None else (10_000_000 if args.full else LOOKBACK_BARS)
    try:
        written = await calculate_indicators(args.symbol, timeframe, lookback)
        log.info("done written=%d", written)
        if args.show > 0:
            pool = await init_pool()
            rows = await _fetch_recent_rows(pool, args.symbol, timeframe, args.show)
            print(
                f"\nLast {len(rows)} indicator rows for {args.symbol} {timeframe} "
                f"(originally requested as '{args.timeframe}'):"
            )
            print(
                f"  {'timestamp':<20}  {'rsi':>9}  {'ema20':>12}  "
                f"{'ema50':>12}  {'ema200':>12}  {'atr':>10}"
            )
            for r in rows:
                ts = r["timestamp"].isoformat(sep=" ", timespec="minutes")
                print(
                    f"  {ts:<20}  {_fmt(r['rsi']):>9}  {_fmt(r['ema20']):>12}  "
                    f"{_fmt(r['ema50']):>12}  {_fmt(r['ema200']):>12}  "
                    f"{_fmt(r['atr']):>10}"
                )
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())
