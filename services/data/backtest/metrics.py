"""Turn a list of simulated trades into the headline performance numbers.

Pure functions over `Trade` objects — no DB, no I/O. Everything a trader needs to
decide whether a strategy has an edge: expectancy (in $ and R), win rate, profit
factor, max drawdown, and the tail risks (consecutive losses).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Sequence

from .engine import RunResult, Trade

ZERO = Decimal("0")


@dataclass(slots=True)
class Metrics:
    strategy: str
    symbol: str
    timeframe: str

    bars_tested: int
    signals_generated: int
    trades: int
    wins: int
    losses: int
    breakeven: int

    win_rate: float                 # 0..1
    gross_profit: float
    gross_loss: float               # negative
    net_pnl: float
    profit_factor: float            # gross_profit / |gross_loss|; inf if no losses

    avg_win: float
    avg_loss: float                 # negative
    expectancy: float               # avg net P&L per trade, $
    expectancy_r: float             # avg R multiple per trade
    payoff_ratio: float             # avg_win / |avg_loss|

    starting_balance: float
    ending_balance: float
    return_pct: float               # (end-start)/start
    max_drawdown: float             # $ peak-to-trough on equity curve
    max_drawdown_pct: float         # % of running peak

    max_consecutive_losses: int
    avg_hold_bars: float
    total_costs: float              # spread embedded in fills is not separable;
                                    # this is the commission component only
    sharpe_per_trade: float         # mean(R)/std(R) — unitless, per-trade basis
    eod_closed: int                 # trades closed by end-of-data (unresolved)
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _f(d: Decimal) -> float:
    return float(d)


def _max_drawdown(equity_curve: Sequence[tuple], starting: Decimal) -> tuple[Decimal, Decimal]:
    """Max peak-to-trough drop in $ and as a fraction of the running peak."""
    peak = starting
    max_dd = ZERO
    max_dd_pct = ZERO
    for _, equity in equity_curve:
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        if peak > ZERO:
            dd_pct = dd / peak
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
    return max_dd, max_dd_pct


def _sharpe(r_multiples: list[Decimal]) -> float:
    n = len(r_multiples)
    if n < 2:
        return 0.0
    vals = [float(r) for r in r_multiples]
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0.0:
        return 0.0
    return (mean / sd) * math.sqrt(n)


def summarize(result: RunResult) -> Metrics:
    trades: list[Trade] = result.trades
    n = len(trades)

    wins = [t for t in trades if t.net_pnl > ZERO]
    losses = [t for t in trades if t.net_pnl < ZERO]
    breakeven = [t for t in trades if t.net_pnl == ZERO]

    gross_profit = sum((t.net_pnl for t in wins), ZERO)
    gross_loss = sum((t.net_pnl for t in losses), ZERO)  # negative
    net_pnl = sum((t.net_pnl for t in trades), ZERO)
    total_commission = sum((t.commission for t in trades), ZERO)

    win_rate = (len(wins) / n) if n else 0.0
    profit_factor = (
        float("inf")
        if gross_loss == ZERO and gross_profit > ZERO
        else (_f(gross_profit) / abs(_f(gross_loss)) if gross_loss != ZERO else 0.0)
    )
    avg_win = (_f(gross_profit) / len(wins)) if wins else 0.0
    avg_loss = (_f(gross_loss) / len(losses)) if losses else 0.0
    expectancy = (_f(net_pnl) / n) if n else 0.0
    r_multiples = [t.r_multiple for t in trades]
    expectancy_r = (sum(_f(r) for r in r_multiples) / n) if n else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss != 0.0 else 0.0

    max_dd, max_dd_pct = _max_drawdown(result.equity_curve, result.starting_balance)

    # Longest losing streak.
    streak = 0
    max_streak = 0
    for t in trades:
        if t.net_pnl < ZERO:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    avg_hold = (sum(t.hold_bars for t in trades) / n) if n else 0.0
    eod = sum(1 for t in trades if t.exit_reason == "eod")

    return_pct = (
        _f((result.ending_balance - result.starting_balance) / result.starting_balance)
        if result.starting_balance > ZERO
        else 0.0
    )

    return Metrics(
        strategy=result.strategy,
        symbol=result.symbol,
        timeframe=result.timeframe,
        bars_tested=result.bars_tested,
        signals_generated=result.signals_generated,
        trades=n,
        wins=len(wins),
        losses=len(losses),
        breakeven=len(breakeven),
        win_rate=win_rate,
        gross_profit=_f(gross_profit),
        gross_loss=_f(gross_loss),
        net_pnl=_f(net_pnl),
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        expectancy_r=expectancy_r,
        payoff_ratio=payoff,
        starting_balance=_f(result.starting_balance),
        ending_balance=_f(result.ending_balance),
        return_pct=return_pct,
        max_drawdown=_f(max_dd),
        max_drawdown_pct=_f(max_dd_pct),
        max_consecutive_losses=max_streak,
        avg_hold_bars=avg_hold,
        total_costs=_f(total_commission),
        sharpe_per_trade=_sharpe(r_multiples),
        eod_closed=eod,
    )
