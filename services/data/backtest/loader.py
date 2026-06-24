"""Load historical bars (candles joined with indicators) from TimescaleDB.

Mirrors the join the live strategy runner uses (`strategy_runner._load_window`),
but pulls the *full* history ascending for replay rather than a small trailing
window.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import asyncpg

from .engine import Bar


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def load_bars(
    pool: asyncpg.Pool,
    symbol: str,
    timeframe: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Bar]:
    """Return bars for (symbol, timeframe), oldest first, with indicators attached.

    Inner-joins Indicator so every returned bar carries its (causal) readings;
    bars without a matching indicator row (the warmup head) are excluded — which
    is correct, since strategies can't act before indicators warm up anyway.
    """
    clauses = ['c."symbol" = $1', 'c."timeframe" = $2']
    params: list[object] = [symbol, timeframe]
    if start is not None:
        params.append(start)
        clauses.append(f'c."timestamp" >= ${len(params)}')
    if end is not None:
        params.append(end)
        clauses.append(f'c."timestamp" <= ${len(params)}')

    sql = f"""
        SELECT c."timestamp" AS ts,
               c."open" AS open, c."high" AS high, c."low" AS low, c."close" AS close,
               i."rsi" AS rsi, i."ema20" AS ema20, i."ema50" AS ema50,
               i."ema200" AS ema200, i."atr" AS atr
        FROM "Candle" c
        JOIN "Indicator" i
          ON i."symbol" = c."symbol"
         AND i."timeframe" = c."timeframe"
         AND i."timestamp" = c."timestamp"
        WHERE {' AND '.join(clauses)}
        ORDER BY c."timestamp" ASC
    """
    rows = await pool.fetch(sql, *params)
    return [
        Bar(
            timestamp=r["ts"],
            open=_dec(r["open"]),       # type: ignore[arg-type]
            high=_dec(r["high"]),       # type: ignore[arg-type]
            low=_dec(r["low"]),         # type: ignore[arg-type]
            close=_dec(r["close"]),     # type: ignore[arg-type]
            rsi=_dec(r["rsi"]),
            ema20=_dec(r["ema20"]),
            ema50=_dec(r["ema50"]),
            ema200=_dec(r["ema200"]),
            atr=_dec(r["atr"]),
        )
        for r in rows
    ]


async def available_series(pool: asyncpg.Pool) -> list[tuple[str, str, int]]:
    """(symbol, timeframe, candle_count) for everything stored — for diagnostics."""
    rows = await pool.fetch(
        'SELECT "symbol", "timeframe", count(*) AS n '
        'FROM "Candle" GROUP BY 1, 2 ORDER BY 1, 2'
    )
    return [(r["symbol"], r["timeframe"], r["n"]) for r in rows]
