"""scalp_vwap through the backtest engine — hermetic (no DB, no pandas).

Guards the volume plumbing: the engine's Bar now carries `volume` into the
IndicatorBar, without which scalp_vwap's VWAP is None and it never trades. Runs
with regime_gating=False so it needs neither the DB nor pandas_ta (the regime gate
itself is covered by test_regime.py). Runnable: ``python tests/test_backtest_scalp_vwap.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import Bar, BacktestConfig, simulate  # noqa: E402
from strategies import build_strategy  # noqa: E402

T0 = datetime(2026, 6, 26, 9, 0, 0)


def _bar(i, c, *, o=None, h=None, l=None, vol=100, ema20=None, ema50=None, atr=None) -> Bar:
    c = Decimal(str(c))
    return Bar(
        timestamp=T0 + timedelta(minutes=i),
        open=Decimal(str(o)) if o is not None else c,
        high=Decimal(str(h)) if h is not None else c,
        low=Decimal(str(l)) if l is not None else c,
        close=c,
        volume=Decimal(str(vol)),
        ema20=Decimal(str(ema20)) if ema20 is not None else None,
        ema50=Decimal(str(ema50)) if ema50 is not None else None,
        atr=Decimal(str(atr)) if atr is not None else None,
    )


def test_volume_flows_into_indicator_bar() -> None:
    ib = _bar(0, 100, vol=137).to_indicator_bar()
    assert ib.volume == Decimal("137")  # without this, VWAP is None and scalp_vwap is silent


def test_scalp_vwap_trades_through_engine() -> None:
    # Ascending series: 21 fillers near 100, a swing high (window high above close),
    # a pullback that retests VWAP, then the bullish confirmation bar (the only bar
    # carrying EMAs/ATR, so it is the only one scalp_vwap can fire on), then one more
    # bar so the entry has a next-bar open to fill against.
    bars: list[Bar] = [_bar(i, 100.0, o=100.0, h=100.2, l=99.85, vol=100) for i in range(21)]
    bars.append(_bar(21, 100.0, o=100.0, h=101.8, l=99.5, vol=100))               # swing high
    bars.append(_bar(22, 100.2, o=100.5, h=100.6, l=99.95, vol=100))              # pullback to VWAP
    bars.append(_bar(23, 100.8, o=100.2, h=101.0, l=100.1, vol=130,               # confirmation
                     ema20=100.6, ema50=100.0, atr=0.5))
    bars.append(_bar(24, 100.7, o=100.85, h=101.0, l=100.5, vol=100))             # next-bar fill

    strat = build_strategy("scalp_vwap", None)
    res = simulate(
        strat, bars, "XAUUSD", "1min",
        BacktestConfig(regime_gating=False, apply_costs=False),
    )
    assert res.signals_generated >= 1          # VWAP computed (volume present) and the setup fired
    assert res.skipped_no_next_bar == 0        # the next-bar fill existed
    assert len(res.trades) == 1                # opened on bar 24's open, force-closed at EOD
    assert res.trades[0].direction == "LONG"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
