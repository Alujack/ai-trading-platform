"""First strategy detector: RSI(14) + EMA200 trend filter.

DEPRECATED (Phase 4): this logic now lives in ``strategies/meanrev_rsi.py`` and
runs through the unified AI + risk gate via ``strategy_runner``. This module
inserts directly into the Signal table and therefore BYPASSES AI validation and
the risk engine — kept only as an offline/backfill tool. Do not schedule it.

Fires LONG when RSI<30 and close>EMA200 (oversold pullback in an uptrend).
Fires SHORT when RSI>70 and close<EMA200 (overbought rally in a downtrend).
SL/TP derived from ATR(14): 1.5*ATR stop, 3.0*ATR target (1:2 RR).

Signal IDs are a deterministic hash of (symbol, timeframe, direction, bar_ts),
so re-runs and backtest sweeps are idempotent — ON CONFLICT (id) DO NOTHING.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import asyncpg
from dotenv import load_dotenv

from db import close_pool, init_pool
from indicator_calculator import normalize_timeframe

log = logging.getLogger("strategy.rsi_ema200")

RSI_OVERSOLD = Decimal("30")
RSI_OVERBOUGHT = Decimal("70")
ATR_STOP_MULT = Decimal("1.5")
ATR_TARGET_MULT = Decimal("3.0")


@dataclass(slots=True)
class Bar:
    bar_ts: datetime
    close: Decimal
    rsi: Decimal
    ema200: Decimal
    atr: Decimal


async def _recent_bars(
    pool: asyncpg.Pool, symbol: str, timeframe: str, limit: int
) -> list[Bar]:
    rows = await pool.fetch(
        """
        SELECT c."timestamp" AS bar_ts, c."close" AS close,
               i."rsi" AS rsi, i."ema200" AS ema200, i."atr" AS atr
        FROM "Candle" c
        JOIN "Indicator" i
          ON i."symbol" = c."symbol"
         AND i."timeframe" = c."timeframe"
         AND i."timestamp" = c."timestamp"
        WHERE c."symbol" = $1 AND c."timeframe" = $2
          AND i."rsi" IS NOT NULL
          AND i."ema200" IS NOT NULL
          AND i."atr" IS NOT NULL
        ORDER BY c."timestamp" DESC
        LIMIT $3
        """,
        symbol,
        timeframe,
        limit,
    )
    return [
        Bar(
            bar_ts=r["bar_ts"],
            close=r["close"],
            rsi=r["rsi"],
            ema200=r["ema200"],
            atr=r["atr"],
        )
        for r in rows
    ]


def _confidence(rsi: Decimal, direction: str) -> int:
    """Map distance from the RSI threshold to a 50–90 confidence score."""
    if direction == "LONG":
        ratio = max(Decimal("0"), RSI_OVERSOLD - rsi) / RSI_OVERSOLD
    else:
        ratio = max(Decimal("0"), rsi - RSI_OVERBOUGHT) / (Decimal("100") - RSI_OVERBOUGHT)
    score = 50 + int(min(Decimal("1"), ratio) * 40)
    return max(0, min(100, score))


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    key = f"{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


INSERT_SQL = """
INSERT INTO "Signal" (
    "id", "symbol", "timeframe", "direction", "entryPrice",
    "stopLoss", "takeProfit", "confidenceScore", "aiReasoning",
    "status", "createdAt"
)
VALUES ($1, $2, $3, $4::"Direction", $5, $6, $7, $8, $9, 'PENDING', NOW())
ON CONFLICT ("id") DO NOTHING
RETURNING "id"
"""


async def _try_insert(
    pool: asyncpg.Pool, symbol: str, timeframe: str, bar: Bar
) -> str | None:
    if bar.rsi < RSI_OVERSOLD and bar.close > bar.ema200:
        direction = "LONG"
        stop = bar.close - ATR_STOP_MULT * bar.atr
        target = bar.close + ATR_TARGET_MULT * bar.atr
        reasoning = (
            f"Bar {bar.bar_ts.isoformat()}: RSI(14)={bar.rsi} < 30 (oversold) "
            f"and close={bar.close} > EMA200={bar.ema200} (uptrend). "
            f"ATR(14)={bar.atr}. SL = close − 1.5·ATR, TP = close + 3·ATR (RR 1:2)."
        )
    elif bar.rsi > RSI_OVERBOUGHT and bar.close < bar.ema200:
        direction = "SHORT"
        stop = bar.close + ATR_STOP_MULT * bar.atr
        target = bar.close - ATR_TARGET_MULT * bar.atr
        reasoning = (
            f"Bar {bar.bar_ts.isoformat()}: RSI(14)={bar.rsi} > 70 (overbought) "
            f"and close={bar.close} < EMA200={bar.ema200} (downtrend). "
            f"ATR(14)={bar.atr}. SL = close + 1.5·ATR, TP = close − 3·ATR (RR 1:2)."
        )
    else:
        return None

    confidence = _confidence(bar.rsi, direction)
    sid = _signal_id(symbol, timeframe, direction, bar.bar_ts)
    inserted = await pool.fetchval(
        INSERT_SQL,
        sid,
        symbol,
        timeframe,
        direction,
        bar.close,
        stop,
        target,
        confidence,
        reasoning,
    )
    if inserted is None:
        log.debug(
            "idempotent symbol=%s tf=%s bar=%s dir=%s",
            symbol,
            timeframe,
            bar.bar_ts,
            direction,
        )
        return None
    log.info(
        "signal symbol=%s tf=%s bar=%s dir=%s conf=%d entry=%s sl=%s tp=%s",
        symbol,
        timeframe,
        bar.bar_ts,
        direction,
        confidence,
        bar.close,
        stop,
        target,
    )
    return inserted


async def scan(
    symbols: list[str], timeframes: list[str], lookback: int
) -> list[str]:
    pool = await init_pool()
    ids: list[str] = []
    for symbol in symbols:
        for tf in timeframes:
            tf_norm = normalize_timeframe(tf)
            try:
                bars = await _recent_bars(pool, symbol, tf_norm, lookback)
                if not bars:
                    log.info(
                        "skip symbol=%s tf=%s reason=no_bars_with_indicators",
                        symbol,
                        tf_norm,
                    )
                    continue
                for bar in bars:
                    sid = await _try_insert(pool, symbol, tf_norm, bar)
                    if sid:
                        ids.append(sid)
            except Exception as exc:  # noqa: BLE001
                log.error("scan_failed symbol=%s tf=%s err=%s", symbol, tf, exc)
    return ids


async def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols", nargs="+", required=True, help="One or more symbols (e.g. XAUUSD EURUSD)"
    )
    parser.add_argument(
        "--timeframes", nargs="+", required=True, help="One or more timeframes (e.g. 60min daily)"
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=1,
        help="Number of most-recent bars to evaluate per (symbol, timeframe). "
        "Default 1 = only the latest. Use a larger value to backfill historical signals.",
    )
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    try:
        ids = await scan(list(args.symbols), list(args.timeframes), args.lookback)
        print(f"\nInserted {len(ids)} new signal(s)")
        for i in ids:
            print(f"  {i}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_cli())
