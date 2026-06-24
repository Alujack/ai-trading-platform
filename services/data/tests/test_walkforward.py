"""Unit tests for the walk-forward harness (fold logic + OOS aggregation).

Pure logic — no DB. The engine is real; bars and a tiny fake strategy are
hand-built. Runnable under pytest, or directly: ``python tests/test_walkforward.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import Bar, BacktestConfig, CostModel, Trade  # noqa: E402
from backtest.walkforward import (  # noqa: E402
    Fold,
    aggregate_oos,
    expand_grid,
    make_folds,
    optimize_window,
    walk_forward,
)
from strategies.base import SignalCandidate  # noqa: E402

TS = datetime(2026, 1, 1, 0, 0, 0)
D = Decimal


def _bar(i, o, h, lo, c):
    return Bar(timestamp=TS + timedelta(hours=i), open=D(str(o)), high=D(str(h)),
               low=D(str(lo)), close=D(str(c)))


def _trade(net, r):
    return Trade(
        symbol="X", timeframe="60min", strategy="t", direction="LONG",
        signal_time=TS, entry_time=TS, exit_time=TS, entry_price=D("100"),
        stop=D("90"), target=D("120"), exit_price=D("100"), size=D("1"),
        risk_amount=D("100"), gross_pnl=D(str(net)), commission=D("0"),
        net_pnl=D(str(net)), r_multiple=D(str(r)), exit_reason="target",
        hold_bars=1, equity_after=D("0"),
    )


# ── fold generation ───────────────────────────────────────────────────────

def test_make_folds_tiles_oos_without_overlap():
    folds = make_folds(100, is_size=40, oos_size=20)
    assert folds[0] == Fold(0, 40, 40, 60)
    assert folds[1] == Fold(20, 60, 60, 80)
    assert folds[2] == Fold(40, 80, 80, 100)
    assert len(folds) == 3
    # OOS slices are contiguous and non-overlapping.
    for a, b in zip(folds, folds[1:]):
        assert a.oos_end == b.oos_start


def test_make_folds_anchored_grows_is_window():
    folds = make_folds(100, is_size=40, oos_size=20, anchored=True)
    assert all(f.is_start == 0 for f in folds)
    assert folds[0].is_end == 40 and folds[1].is_end == 60  # IS expands each step


def test_make_folds_too_little_data_yields_none():
    assert make_folds(30, is_size=40, oos_size=20) == []


def test_make_folds_rejects_nonpositive():
    for bad in [(100, 0, 20), (100, 40, 0)]:
        try:
            make_folds(*bad)
            assert False, "expected ValueError"
        except ValueError:
            pass


# ── grid expansion ────────────────────────────────────────────────────────

def test_expand_grid_cartesian_product():
    combos = expand_grid({"a": [1, 2], "b": [3, 4]})
    assert {tuple(sorted(c.items())) for c in combos} == {
        (("a", 1), ("b", 3)), (("a", 1), ("b", 4)),
        (("a", 2), ("b", 3)), (("a", 2), ("b", 4)),
    }


def test_expand_empty_grid_is_single_default():
    assert expand_grid({}) == [{}]


# ── OOS aggregation in R-space ─────────────────────────────────────────────

def test_aggregate_oos_basic():
    trades = [_trade(200, 2), _trade(-100, -1), _trade(-100, -1), _trade(200, 2)]
    s = aggregate_oos(trades)
    assert s.trades == 4 and s.wins == 2
    assert abs(s.win_rate - 0.5) < 1e-9
    assert abs(s.profit_factor - 2.0) < 1e-9          # 400 / 200
    assert abs(s.expectancy_r - 0.5) < 1e-9           # (2-1-1+2)/4
    assert abs(s.total_r - 2.0) < 1e-9
    assert s.max_consecutive_losses == 2


def test_aggregate_oos_drawdown_in_r():
    # Cumulative R: +2, +1, 0, -1 (peak 2 -> trough -1 => 3R drawdown)
    trades = [_trade(200, 2), _trade(-100, -1), _trade(-100, -1), _trade(-100, -1)]
    s = aggregate_oos(trades)
    assert abs(s.max_drawdown_r - 3.0) < 1e-9


def test_aggregate_oos_empty_is_safe():
    s = aggregate_oos([])
    assert s.trades == 0 and s.expectancy_r == 0.0 and s.total_r == 0.0


# ── strategy used for the integration-style tests ─────────────────────────-

class StopMultStrategy:
    """A LONG-only fake whose edge depends on a param: it only emits when the
    `atrStopMult` param equals `winning_mult`. Lets us prove the optimiser picks
    the param that worked in-sample and that it carries into OOS."""

    def __init__(self, params=None):
        p = params or {}
        self.name = "stopmult"
        self.regimes = set()  # short windows classify UNKNOWN -> gate fails open
        self.mult = float(p.get("atrStopMult", 1.5))
        self._winning_mult = 2.0

    def evaluate(self, window):
        bar = window.latest
        if bar is None or self.mult != self._winning_mult:
            return []
        if bar.close != D("100"):
            return []
        return [SignalCandidate(self.name, window.symbol, window.timeframe, "LONG",
                                bar.close, D("90"), D("120"), 0, "fake")]


def _register_fake(monkeypatch_dict):
    from strategies import registry
    registry.STRATEGY_FACTORIES["stopmult"] = StopMultStrategy
    return registry


def _winning_series():
    """Trigger-bar (close=100) followed by a target hit, repeated, so the
    'winning' param produces a string of +2R trades."""
    bars = []
    for block in range(8):
        t = block * 4
        bars.append(_bar(t, 100, 100, 100, 100))        # signal (close == 100)
        bars.append(_bar(t + 1, 100, 100, 100, 100))    # entry fill @100
        bars.append(_bar(t + 2, 100, 120, 100, 110))    # target 120 hit (+2R)
        bars.append(_bar(t + 3, 105, 105, 105, 105))    # reset (no trigger)
    return bars


def _cfg():
    return BacktestConfig(starting_balance=D("10000"), risk_pct=D("1"),
                          cost=CostModel(), apply_costs=False, regime_gating=False)


def test_optimize_window_picks_the_param_that_traded():
    _register_fake(None)
    bars = _winning_series()
    grid = [{"atrStopMult": 1.0}, {"atrStopMult": 2.0}]  # only 2.0 trades
    best = optimize_window("stopmult", bars, "X", "60min", _cfg(), grid,
                           objective="total_r", min_trades=1)
    assert best.params == {"atrStopMult": 2.0}
    assert best.is_trades > 0 and best.score > 0


def test_walk_forward_optimized_carries_param_into_oos():
    _register_fake(None)
    bars = _winning_series()  # 32 bars
    grid = {"atrStopMult": [1.0, 2.0]}
    res = walk_forward("stopmult", bars, "X", "60min", _cfg(),
                       is_size=12, oos_size=8, grid=grid, optimize=True,
                       objective="total_r", min_trades=1)
    assert len(res.folds) >= 1
    # Every fold should have selected the winning param and made money OOS.
    assert all(f.params == {"atrStopMult": 2.0} for f in res.folds)
    assert res.oos.trades > 0
    assert res.oos.total_r > 0
    assert res.profitable_folds == len(res.folds)
    # IS edge existed and persisted OOS -> efficiency should be solidly positive.
    assert res.walk_forward_efficiency is not None and res.walk_forward_efficiency > 0.5


def test_walk_forward_no_optimize_uses_defaults():
    _register_fake(None)
    bars = _winning_series()
    # Default atrStopMult is 1.5 (not the winning 2.0), so fixed-params mode trades nothing.
    res = walk_forward("stopmult", bars, "X", "60min", _cfg(),
                       is_size=12, oos_size=8, optimize=False)
    assert res.optimized is False
    assert res.oos.trades == 0
    assert res.walk_forward_efficiency is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
