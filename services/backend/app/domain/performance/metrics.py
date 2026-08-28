"""Performance metrics — port of `apps/api/src/services/performance.ts`.

Pure and dependency-free, so the boundary behaviour (a zero-loss book giving an
infinite profit factor, drawdown measured on the running P&L curve) is unit
tested directly against the TypeScript test cases.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ...core.serialization import js_number


@dataclass(slots=True)
class TradeStats:
    entryPrice: float
    exitPrice: float | None
    profitLoss: float | None
    direction: str
    stopLoss: float


def _round2(value: float) -> float:
    """`Math.round(n * 100) / 100` — JS half-up on the scaled value."""
    if not math.isfinite(value):
        return value
    return math.floor(value * 100 + 0.5) / 100 if value >= 0 else -(math.floor(-value * 100 + 0.5) / 100)


def compute_performance(trades: list[TradeStats]) -> dict[str, float | int | None]:
    """Aggregate closed trades into the dashboard's performance payload.

    Expectancy = average P&L per trade (the real edge metric — what win rate
    alone cannot tell you). Profit factor = gross profit ÷ gross loss (>1 = edge).
    """
    total_pnl = 0.0
    wins = 0
    rr_sum = 0.0
    rr_count = 0
    running_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0
    gross_profit = 0.0
    gross_loss = 0.0

    for t in trades:
        pnl = t.profitLoss or 0.0
        total_pnl += pnl
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)

        if t.exitPrice is not None:
            risk = abs(t.entryPrice - t.stopLoss)
            if risk > 0:
                reward = (
                    t.exitPrice - t.entryPrice
                    if t.direction == "LONG"
                    else t.entryPrice - t.exitPrice
                )
                rr_sum += reward / risk
                rr_count += 1

        running_pnl += pnl
        peak_pnl = max(peak_pnl, running_pnl)
        max_drawdown = max(max_drawdown, peak_pnl - running_pnl)

    count = len(trades)
    if gross_loss > 0:
        profit_factor: float = _round2(gross_profit / gross_loss)
    elif gross_profit > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    return {
        "totalTrades": count,
        "winRate": js_number(_round2((wins / count) * 100) if count else 0),
        "totalPnL": js_number(_round2(total_pnl)),
        "maxDrawdown": js_number(_round2(max_drawdown)),
        "averageRR": js_number(_round2(rr_sum / rr_count) if rr_count else 0),
        "expectancy": js_number(_round2(total_pnl / count) if count else 0),
        "profitFactor": js_number(profit_factor),
    }
