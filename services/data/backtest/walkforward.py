"""Walk-forward validation — the test that separates a real edge from a curve fit.

A single backtest over the whole history answers "did this strategy make money
on this data?". That question is almost worthless on its own: with enough
parameter choices and enough hindsight, *something* always looks good (the
multiple-comparisons trap). The question that matters is "would the strategy
have made money on data it was never tuned on?" — and that is what walk-forward
answers.

How it works
------------
The bar series is sliced into consecutive (in-sample, out-of-sample) folds:

    |==== IS ====|= OOS =|
              |==== IS ====|= OOS =|
                        |==== IS ====|= OOS =|   ...

For each fold we (optionally) optimise the strategy's parameters on the IS
window, then evaluate those *frozen* parameters on the immediately-following OOS
window — data the optimiser never saw. Concatenating every OOS slice gives one
continuous out-of-sample track record, which is the honest estimate of live
behaviour.

Two knobs of rigour:
  * `optimize=True`  → grid-search params on each IS window (true walk-forward).
                       Reports walk-forward efficiency (OOS edge ÷ IS edge): near
                       or above 1.0 is robust; near 0 or negative is overfit.
  * `optimize=False` → freeze the strategy's default params and just measure OOS
                       consistency across time. Directly tests the LIVE config.

Aggregation is done in R-space (per-trade risk multiples), not dollars, so the
result doesn't depend on how equity happened to compound inside each fold and
folds are directly comparable. Everything here is pure given `engine.simulate`;
no DB, no network — the CLI (`walkforward.py`) handles data loading.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from strategies import build_strategy
from strategies.base import Strategy

from .engine import Bar, BacktestConfig, Trade, simulate

ZERO = Decimal("0")


# ── fold generation ────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class Fold:
    """Index ranges (half-open) into the bar list for one walk-forward step."""

    is_start: int
    is_end: int      # exclusive
    oos_start: int
    oos_end: int     # exclusive

    @property
    def is_len(self) -> int:
        return self.is_end - self.is_start

    @property
    def oos_len(self) -> int:
        return self.oos_end - self.oos_start


def make_folds(n_bars: int, is_size: int, oos_size: int, *, anchored: bool = False) -> list[Fold]:
    """Roll an (IS, OOS) window across `n_bars`, stepping by `oos_size` so the
    OOS slices tile the timeline without overlap (no bar is tested twice).

    `anchored=True` keeps the IS window's start pinned at 0 and grows it each
    step (expanding window); the default is a rolling window of fixed `is_size`.
    """
    if is_size <= 0 or oos_size <= 0:
        raise ValueError("is_size and oos_size must be positive")
    folds: list[Fold] = []
    oos_start = is_size
    while oos_start + oos_size <= n_bars:
        is_start = 0 if anchored else oos_start - is_size
        folds.append(Fold(is_start, oos_start, oos_start, oos_start + oos_size))
        oos_start += oos_size
    return folds


# ── parameter grid ───────────────────────────────────────────────────────--

def expand_grid(grid: dict[str, Sequence]) -> list[dict]:
    """Cartesian product of a {param: [values]} grid into a list of param dicts.

    An empty grid yields a single empty dict (i.e. "use strategy defaults"),
    which is exactly the `optimize=False` behaviour.
    """
    if not grid:
        return [{}]
    keys = list(grid.keys())
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


# ── per-window evaluation + optimisation ─────────────────────────────────--

def _run(strategy: Strategy, bars: Sequence[Bar], symbol: str, tf: str, cfg: BacktestConfig) -> list[Trade]:
    return simulate(strategy, bars, symbol, tf, cfg).trades


def _expectancy_r(trades: Sequence[Trade]) -> float:
    if not trades:
        return 0.0
    return sum(float(t.r_multiple) for t in trades) / len(trades)


def _objective_value(trades: Sequence[Trade], objective: str) -> float:
    """Score a parameter set on a window. Higher is better."""
    if objective == "expectancy_r":
        return _expectancy_r(trades)
    if objective == "profit_factor":
        gp = sum(float(t.net_pnl) for t in trades if t.net_pnl > ZERO)
        gl = sum(float(t.net_pnl) for t in trades if t.net_pnl < ZERO)
        if gl == 0.0:
            return gp  # no losses: rank by gross profit so ties break sensibly
        return gp / abs(gl)
    if objective == "total_r":
        return sum(float(t.r_multiple) for t in trades)
    raise ValueError(f"unknown objective '{objective}'")


@dataclass(slots=True)
class OptResult:
    params: dict
    score: float
    is_trades: int


def optimize_window(
    strategy_name: str,
    bars: Sequence[Bar],
    symbol: str,
    tf: str,
    cfg: BacktestConfig,
    param_sets: list[dict],
    *,
    objective: str = "expectancy_r",
    min_trades: int = 5,
) -> OptResult:
    """Pick the best param set on an in-sample window.

    Candidates that produced fewer than `min_trades` trades in-sample are
    disqualified unless none clear the bar (then we fall back to whichever
    traded most, so a fold never silently produces nothing). With a single
    candidate (e.g. the default-params case) this is just an evaluation.
    """
    best: OptResult | None = None
    fallback: OptResult | None = None
    for params in param_sets:
        strat = build_strategy(strategy_name, params or None)
        trades = _run(strat, bars, symbol, tf, cfg)
        score = _objective_value(trades, objective)
        cand = OptResult(params=params, score=score, is_trades=len(trades))
        if fallback is None or cand.is_trades > fallback.is_trades:
            fallback = cand
        if len(trades) >= min_trades and (best is None or score > best.score):
            best = cand
    return best or fallback or OptResult({}, 0.0, 0)


# ── out-of-sample aggregation (R-space) ──────────────────────────────────--

@dataclass(slots=True)
class OosStats:
    trades: int
    wins: int
    win_rate: float
    profit_factor: float        # from net P&L
    expectancy_r: float         # mean R per trade
    total_r: float              # summed R (the OOS "equity curve" unit)
    max_drawdown_r: float       # peak-to-trough on the cumulative-R curve
    max_consecutive_losses: int
    sharpe_per_trade: float     # mean(R)/std(R) * sqrt(n) — unitless aggregate


def aggregate_oos(trades: Sequence[Trade]) -> OosStats:
    """Headline stats over a concatenated out-of-sample trade list, computed in
    R-multiples so folds with different equity levels combine cleanly."""
    n = len(trades)
    if n == 0:
        return OosStats(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

    rs = [float(t.r_multiple) for t in trades]
    wins = sum(1 for t in trades if t.net_pnl > ZERO)
    gp = sum(float(t.net_pnl) for t in trades if t.net_pnl > ZERO)
    gl = sum(float(t.net_pnl) for t in trades if t.net_pnl < ZERO)
    pf = float("inf") if gl == 0.0 and gp > 0 else (gp / abs(gl) if gl != 0.0 else 0.0)

    # Cumulative-R curve → max drawdown in R.
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for t in trades:
        cum += float(t.r_multiple)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if t.net_pnl < ZERO:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    mean = sum(rs) / n
    if n >= 2:
        var = sum((r - mean) ** 2 for r in rs) / (n - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(n) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    return OosStats(
        trades=n,
        wins=wins,
        win_rate=wins / n,
        profit_factor=pf,
        expectancy_r=mean,
        total_r=sum(rs),
        max_drawdown_r=max_dd,
        max_consecutive_losses=max_streak,
        sharpe_per_trade=sharpe,
    )


# ── the walk-forward run ─────────────────────────────────────────────────--

@dataclass(slots=True)
class FoldReport:
    fold: Fold
    params: dict
    is_trades: int
    is_score: float
    oos: OosStats


@dataclass(slots=True)
class WalkForwardResult:
    strategy: str
    symbol: str
    timeframe: str
    optimized: bool
    objective: str
    folds: list[FoldReport] = field(default_factory=list)
    oos: OosStats = field(default_factory=lambda: aggregate_oos([]))
    profitable_folds: int = 0
    walk_forward_efficiency: float | None = None  # OOS expectancy ÷ IS expectancy


def walk_forward(
    strategy_name: str,
    bars: Sequence[Bar],
    symbol: str,
    tf: str,
    cfg: BacktestConfig,
    *,
    is_size: int,
    oos_size: int,
    grid: dict[str, Sequence] | None = None,
    optimize: bool = True,
    objective: str = "expectancy_r",
    min_trades: int = 5,
    anchored: bool = False,
) -> WalkForwardResult:
    """Run walk-forward over `bars` and return per-fold + aggregate OOS results.

    When `optimize` is False the grid is ignored and each fold uses the
    strategy's default params — a pure out-of-sample consistency test of the
    live configuration.
    """
    param_sets = expand_grid(grid or {}) if optimize else [{}]
    folds = make_folds(len(bars), is_size, oos_size, anchored=anchored)

    reports: list[FoldReport] = []
    all_oos: list[Trade] = []
    is_expectancies: list[float] = []
    oos_expectancies: list[float] = []

    for fold in folds:
        is_bars = bars[fold.is_start : fold.is_end]
        oos_bars = bars[fold.oos_start : fold.oos_end]

        opt = optimize_window(
            strategy_name, is_bars, symbol, tf, cfg, param_sets,
            objective=objective, min_trades=min_trades,
        )
        # Freeze the IS-selected params, evaluate on unseen OOS bars.
        oos_trades = _run(build_strategy(strategy_name, opt.params or None), oos_bars, symbol, tf, cfg)
        oos_stats = aggregate_oos(oos_trades)

        reports.append(FoldReport(fold, opt.params, opt.is_trades, opt.score, oos_stats))
        all_oos.extend(oos_trades)

        # For walk-forward efficiency, compare like-for-like: the selected
        # params' IS expectancy vs their OOS expectancy on this fold.
        is_trades = _run(build_strategy(strategy_name, opt.params or None), is_bars, symbol, tf, cfg)
        is_expectancies.append(_expectancy_r(is_trades))
        oos_expectancies.append(oos_stats.expectancy_r)

    agg = aggregate_oos(all_oos)
    profitable = sum(1 for r in reports if r.oos.total_r > 0)

    wfe: float | None = None
    if optimize and reports:
        mean_is = sum(is_expectancies) / len(is_expectancies)
        mean_oos = sum(oos_expectancies) / len(oos_expectancies)
        # Efficiency only meaningful when IS actually had a positive edge to keep.
        if mean_is > 0:
            wfe = mean_oos / mean_is

    return WalkForwardResult(
        strategy=strategy_name,
        symbol=symbol,
        timeframe=tf,
        optimized=optimize,
        objective=objective,
        folds=reports,
        oos=agg,
        profitable_folds=profitable,
        walk_forward_efficiency=wfe,
    )
