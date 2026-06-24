"""Unit tests for the backtest engine + metrics, on hand-built synthetic bars.

Pure logic — no DB. Runnable under pytest, or directly:
``python tests/test_backtest.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import (  # noqa: E402
    Bar,
    BacktestConfig,
    CostModel,
    RunResult,
    Trade,
    simulate,
)
from backtest.metrics import summarize  # noqa: E402
from regime import UNKNOWN, compute_regime  # noqa: E402
from strategies.base import RANGING, TRENDING, SignalCandidate  # noqa: E402

TS = datetime(2026, 6, 23, 12, 0, 0)
D = Decimal


def approx(a, b, tol="0.01"):
    """Decimal-friendly closeness check. Position size is risk/distance, a
    non-terminating Decimal, so P&L lands a hair off round numbers."""
    return abs(D(str(a)) - D(str(b))) < D(tol)


class FakeStrategy:
    """Emits one candidate whenever the current bar's close matches `trigger`."""

    def __init__(self, name, direction, trigger, stop, target, cooldown_ms=None, regimes=None):
        self.name = name
        # Empty set by default keeps legacy tests unaffected: their windows are
        # too short for the regime classifier (returns UNKNOWN -> fail open).
        self.regimes = set(regimes) if regimes is not None else set()
        self._direction = direction
        self._trigger = D(str(trigger))
        self._stop = D(str(stop))
        self._target = D(str(target))
        self._cooldown_ms = cooldown_ms

    def evaluate(self, window):
        bar = window.latest
        if bar is None or bar.close != self._trigger:
            return []
        return [
            SignalCandidate(
                strategy_name=self.name,
                symbol=window.symbol,
                timeframe=window.timeframe,
                direction=self._direction,
                entry=bar.close,
                stop=self._stop,
                target=self._target,
                confidence=0,
                reasoning="fake",
                cooldown_ms=self._cooldown_ms,
            )
        ]


def _bar(i, o, h, l, c):
    return Bar(timestamp=TS + timedelta(hours=i), open=D(str(o)), high=D(str(h)),
               low=D(str(l)), close=D(str(c)))


def _cfg(**kw):
    base = dict(starting_balance=D("10000"), risk_pct=D("1"),
               cost=CostModel(), apply_costs=False)
    base.update(kw)
    return BacktestConfig(**base)


def _run(strategy, bars, cfg):
    return simulate(strategy, bars, "XAUUSD", "60min", cfg)


# ── entry / exit mechanics ────────────────────────────────────────────────

def test_long_target_hit_no_costs():
    strat = FakeStrategy("t", "LONG", trigger=100, stop=91, target=118)
    bars = [
        _bar(0, 100, 100, 100, 100),  # signal bar
        _bar(1, 100, 100, 100, 100),  # entry fills at open=100, no touch
        _bar(2, 100, 118, 100, 110),  # high reaches target -> win
    ]
    res = _run(strat, bars, _cfg())
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "target"
    assert t.entry_price == D("100")
    # size = (10000*1%)/|100-91| = 100/9 ; pnl = (118-100)*size = 200
    assert approx(t.net_pnl, "200")
    assert approx(t.r_multiple, "2", tol="0.0001")
    assert approx(res.ending_balance, "10200")


def test_long_stop_hit_loses_one_R():
    strat = FakeStrategy("t", "LONG", trigger=100, stop=91, target=118)
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),
        _bar(2, 100, 105, 91, 95),    # low reaches stop -> loss
    ]
    res = _run(strat, bars, _cfg())
    t = res.trades[0]
    assert t.exit_reason == "stop"
    assert approx(t.net_pnl, "-100")     # -1R with no costs
    assert approx(t.r_multiple, "-1", tol="0.0001")


def test_ambiguous_bar_assumes_stop_first():
    strat = FakeStrategy("t", "LONG", trigger=100, stop=91, target=118)
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),
        _bar(2, 100, 118, 91, 100),   # BOTH stop and target inside this bar
    ]
    res = _run(strat, bars, _cfg())
    assert res.trades[0].exit_reason == "stop"  # conservative


def test_entry_fills_on_next_bar_open_not_signal_close():
    strat = FakeStrategy("t", "LONG", trigger=100, stop=91, target=118)
    bars = [
        _bar(0, 100, 100, 100, 100),  # signal at close=100
        _bar(1, 101, 101, 101, 101),  # GAP up: entry must fill at open=101
        _bar(2, 101, 118, 101, 110),
    ]
    res = _run(strat, bars, _cfg())
    t = res.trades[0]
    assert t.entry_price == D("101")
    # size = 100/|101-91| = 10 ; pnl = (118-101)*10 = 170
    assert t.net_pnl == D("170")


def test_short_target_hit():
    strat = FakeStrategy("t", "SHORT", trigger=100, stop=103, target=94)
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),
        _bar(2, 100, 100, 94, 96),    # low reaches target -> win for short
    ]
    res = _run(strat, bars, _cfg())
    t = res.trades[0]
    assert t.direction == "SHORT"
    assert t.exit_reason == "target"
    # size = 100/|100-103| = 33.33..; pnl = (100-94)*size = 200
    assert approx(t.net_pnl, "200")


# ── cost model ────────────────────────────────────────────────────────────

def test_costs_reduce_pnl_via_spread():
    strat = FakeStrategy("t", "LONG", trigger=100, stop=91, target=118)
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),
        _bar(2, 100, 118, 100, 110),
    ]
    cfg = _cfg(apply_costs=True, cost=CostModel(spread=D("2")))  # half-spread = 1
    res = _run(strat, bars, cfg)
    t = res.trades[0]
    assert t.entry_price == D("101")           # 100 + half spread
    assert t.exit_price == D("117")            # 118 - half spread
    # size = 100/|101-91| = 10 ; pnl = (117-101)*10 = 160 < 200 (no-cost case)
    assert t.net_pnl == D("160")


def test_commission_is_charged_round_turn():
    strat = FakeStrategy("t", "LONG", trigger=100, stop=90, target=120)
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),
        _bar(2, 100, 120, 100, 115),
    ]
    cfg = _cfg(apply_costs=True, cost=CostModel(commission_bps=D("10")))  # 0.1%/side
    res = _run(strat, bars, cfg)
    t = res.trades[0]
    # size = 100/10 = 10 ; notional = 100*10 = 1000 ; commission = 1000*0.001*2 = 2
    assert t.commission == D("2")
    assert t.net_pnl == t.gross_pnl - D("2")


# ── position sizing / compounding ─────────────────────────────────────────

def test_risk_is_one_percent_of_current_equity():
    strat = FakeStrategy("t", "LONG", trigger=100, stop=90, target=110)
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),
        _bar(2, 100, 110, 100, 105),
    ]
    res = _run(strat, bars, _cfg())  # 1% of 10000 = 100 risk
    assert res.trades[0].risk_amount == D("100")


# ── single-position + cooldown ────────────────────────────────────────────

def test_no_overlapping_positions():
    # Trigger fires on every close==100 bar, but while a trade is open the engine
    # must ignore new signals.
    strat = FakeStrategy("t", "LONG", trigger=100, stop=91, target=109)
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),  # entry
        _bar(2, 100, 100, 100, 100),  # would re-trigger, but position is open
        _bar(3, 100, 109, 100, 105),  # target hit, closes
    ]
    res = _run(strat, bars, _cfg())
    assert len(res.trades) == 1


# ── regime gating (backtest must mirror the live runner) ──────────────────

def _trending_series():
    """40 steadily-rising bars (classifies TRENDING), then a signal bar whose
    close == 180, then a fill bar whose high reaches the 200 target. The signal
    fires late enough that ADX has warmed up, so the regime is a real (non-
    UNKNOWN) reading at decision time — exactly the case the gate must handle."""
    bars = [_bar(i, 100 + i * 2, 100 + i * 2 + 1, 100 + i * 2 - 1, 100 + i * 2) for i in range(40)]
    bars.append(_bar(40, 180, 181, 179, 180))   # signal: close == trigger
    bars.append(_bar(41, 180, 200, 180, 190))   # entry fills at 180, high hits target 200
    return bars


def _regime_at_signal(bars):
    hl = bars[:41]  # causal window ending at the signal bar
    return compute_regime(
        [float(b.high) for b in hl], [float(b.low) for b in hl], [float(b.close) for b in hl]
    ).regime


def test_regime_is_trending_precondition():
    # Guards the other tests: if this series stops classifying as TRENDING, the
    # gating assertions below would silently become meaningless.
    assert _regime_at_signal(_trending_series()) == TRENDING


def test_regime_gate_blocks_strategy_that_doesnt_trade_this_regime():
    bars = _trending_series()
    strat = FakeStrategy("range_only", "LONG", trigger=180, stop=170, target=200, regimes={RANGING})
    res = _run(strat, bars, _cfg())  # gating defaults ON (mirrors live)
    assert res.trades == []          # suppressed: regime is TRENDING, strategy trades RANGING
    assert res.regime_gated == 1


def test_regime_gate_allows_strategy_that_trades_this_regime():
    bars = _trending_series()
    strat = FakeStrategy("trend_ok", "LONG", trigger=180, stop=170, target=200, regimes={TRENDING})
    res = _run(strat, bars, _cfg())
    assert len(res.trades) == 1
    assert res.regime_gated == 0
    assert res.trades[0].exit_reason == "target"


def test_no_regime_gate_flag_restores_ungated_behaviour():
    bars = _trending_series()
    strat = FakeStrategy("range_only", "LONG", trigger=180, stop=170, target=200, regimes={RANGING})
    res = _run(strat, bars, _cfg(regime_gating=False))
    assert len(res.trades) == 1      # same setup as the blocked case, now allowed
    assert res.regime_gated == 0


def test_unknown_regime_fails_open():
    # Too few bars for ADX -> UNKNOWN -> the gate must NOT suppress (fail open),
    # so a RANGING-only strategy still trades. This is also why the short-window
    # legacy tests above are unaffected by gating defaulting on.
    strat = FakeStrategy("range_only", "LONG", trigger=100, stop=91, target=118, regimes={RANGING})
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 100, 100, 100),
        _bar(2, 100, 118, 100, 110),
    ]
    assert _regime_at_signal(bars) == UNKNOWN  # precondition: ADX can't warm up here
    res = _run(strat, bars, _cfg())
    assert len(res.trades) == 1
    assert res.regime_gated == 0


# ── metrics ───────────────────────────────────────────────────────────────

def _trade(net, r, reason="target", hold=2):
    return Trade(
        symbol="X", timeframe="60min", strategy="t", direction="LONG",
        signal_time=TS, entry_time=TS, exit_time=TS, entry_price=D("100"),
        stop=D("90"), target=D("120"), exit_price=D("100"), size=D("1"),
        risk_amount=D("100"), gross_pnl=D(str(net)), commission=D("0"),
        net_pnl=D(str(net)), r_multiple=D(str(r)), exit_reason=reason,
        hold_bars=hold, equity_after=D("0"),
    )


def test_metrics_basic_stats():
    trades = [_trade(200, 2), _trade(-100, -1), _trade(-100, -1)]
    curve = [(TS, D("10200")), (TS, D("10100")), (TS, D("10000"))]
    res = RunResult(
        symbol="X", timeframe="60min", strategy="t", trades=trades,
        equity_curve=curve, starting_balance=D("10000"), ending_balance=D("10000"),
        bars_tested=10, signals_generated=3, skipped_no_next_bar=0,
    )
    m = summarize(res)
    assert m.trades == 3
    assert m.wins == 1 and m.losses == 2
    assert abs(m.win_rate - 1 / 3) < 1e-9
    assert m.gross_profit == 200.0
    assert m.gross_loss == -200.0
    assert m.profit_factor == 1.0
    assert m.expectancy == 0.0
    assert m.max_consecutive_losses == 2
    # peak 10200 -> trough 10000 => dd 200
    assert m.max_drawdown == 200.0
    assert abs(m.max_drawdown_pct - 200 / 10200) < 1e-9


def test_profit_factor_infinite_when_no_losses():
    trades = [_trade(50, 0.5), _trade(75, 0.75)]
    res = RunResult(
        symbol="X", timeframe="60min", strategy="t", trades=trades,
        equity_curve=[(TS, D("10125"))], starting_balance=D("10000"),
        ending_balance=D("10125"), bars_tested=5, signals_generated=2,
        skipped_no_next_bar=0,
    )
    m = summarize(res)
    assert m.profit_factor == float("inf")


def test_empty_result_is_safe():
    res = RunResult(
        symbol="X", timeframe="60min", strategy="t", trades=[],
        equity_curve=[], starting_balance=D("10000"), ending_balance=D("10000"),
        bars_tested=0, signals_generated=0, skipped_no_next_bar=0,
    )
    m = summarize(res)
    assert m.trades == 0 and m.win_rate == 0.0 and m.net_pnl == 0.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
