"""Unit tests for scalp_sniper, on hand-built OHLCV fixtures.

Mirrors test_scalp_vwap.py. Runnable under pytest, or directly:
``python tests/test_scalp_sniper.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import BarWindow, IndicatorBar, build_strategy  # noqa: E402

TS = datetime(2026, 7, 8, 12, 0, 0)


def _bar(c, *, o=None, h=None, l=None, vol=100, ema20=None, ema50=None, atr=None) -> IndicatorBar:
    c = Decimal(str(c))
    return IndicatorBar(
        timestamp=TS,
        close=c,
        open=Decimal(str(o)) if o is not None else c,
        high=Decimal(str(h)) if h is not None else c,
        low=Decimal(str(l)) if l is not None else c,
        volume=Decimal(str(vol)),
        ema20=Decimal(str(ema20)) if ema20 is not None else None,
        ema50=Decimal(str(ema50)) if ema50 is not None else None,
        atr=Decimal(str(atr)) if atr is not None else None,
    )


def _burst_window(*, latest: IndicatorBar | None = None) -> BarWindow:
    """A clean 3-bar UP burst with expanding range breaking the window high.

    Flat base ~100.0 (small ranges, tiny wicks), then UP bars 100.1 -> 100.35 ->
    trigger 100.9 (range 0.7, exceeds prior ranges 0.3/0.25, closes above every
    prior high, 80%+ body, strong volume). ATR 0.5. VWAP sits near the base, so
    price > VWAP and the recent burst tilts the slope up.
    """
    trigger = latest or _bar(
        100.9, o=100.34, h=101.0, l=100.3, vol=160, ema20=100.4, ema50=100.1, atr=0.5
    )
    b1 = _bar(100.35, o=100.12, h=100.4, l=100.1, vol=120)   # UP, range 0.30
    b2 = _bar(100.1, o=99.98, h=100.15, l=99.9, vol=110)     # UP, range 0.25
    fillers = [_bar(99.95, o=99.95, h=100.05, l=99.85, vol=100) for _ in range(22)]
    bars = [trigger, b1, b2, *fillers]  # most-recent first; 25 bars
    return BarWindow(symbol="XAUUSD", timeframe="1min", bars=bars)


def test_scalp_sniper_emits_long_on_burst_with_expansion_breakout() -> None:
    strat = build_strategy("scalp_sniper", None)
    out = strat.evaluate(_burst_window())
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.strategy_name == "scalp_sniper"
    assert c.entry == Decimal("100.9")
    # Structural stop: min low of the 3 burst bars (99.9) - 0.5*ATR(0.25) = 99.65,
    # but floored at close - 1.0*ATR = 100.4 -> the LOWER (safer) of the two wins:
    # stop = min(99.65, 100.4) = 99.65; risk = 1.25; target = entry + 2*risk.
    assert c.stop == Decimal("99.65")
    assert c.target == Decimal("100.9") + Decimal("2") * (Decimal("100.9") - Decimal("99.65"))
    assert 50 <= c.confidence <= 90
    assert c.client_id is not None and len(c.client_id) == 24


def test_scalp_sniper_skips_without_range_expansion() -> None:
    # Trigger range (0.2) does NOT exceed the prior bars' ranges -> fading thrust.
    strat = build_strategy("scalp_sniper", None)
    latest = _bar(100.9, o=100.75, h=100.95, l=100.75, vol=160, ema20=100.4, ema50=100.1, atr=0.5)
    assert strat.evaluate(_burst_window(latest=latest)) == []


def test_scalp_sniper_skips_when_ema_stack_disagrees() -> None:
    # Burst is UP but EMA stack is bearish -> countertrend, skip.
    strat = build_strategy("scalp_sniper", None)
    latest = _bar(100.9, o=100.34, h=101.0, l=100.3, vol=160, ema20=100.1, ema50=100.4, atr=0.5)
    assert strat.evaluate(_burst_window(latest=latest)) == []


def test_scalp_sniper_skips_on_wick_absorption() -> None:
    # Two big upper wicks (>= 0.5*ATR = 0.25) among recent bars -> sellers absorbing.
    strat = build_strategy("scalp_sniper", None)
    w = _burst_window()
    trigger = _bar(100.9, o=100.34, h=101.3, l=100.3, vol=160, ema20=100.4, ema50=100.1, atr=0.5)
    b1 = _bar(100.35, o=100.12, h=100.75, l=100.1, vol=120)  # upper wick 0.40 >= 0.25
    bars = [trigger, b1, *w.bars[2:]]
    assert strat.evaluate(BarWindow(symbol="XAUUSD", timeframe="1min", bars=bars)) == []


def test_scalp_sniper_skips_low_volume_burst() -> None:
    # Trigger volume 50 vs avg ~100 -> below volMinRatio 1.0, no conviction.
    strat = build_strategy("scalp_sniper", None)
    latest = _bar(100.9, o=100.34, h=101.0, l=100.3, vol=50, ema20=100.4, ema50=100.1, atr=0.5)
    assert strat.evaluate(_burst_window(latest=latest)) == []


def test_scalp_sniper_emits_short_on_mirror_burst() -> None:
    # Mirror of the long fixture: 3 DN bars, expanding range, breaking the window low.
    strat = build_strategy("scalp_sniper", None)
    trigger = _bar(99.1, o=99.66, h=99.7, l=99.0, vol=160, ema20=99.6, ema50=99.9, atr=0.5)
    b1 = _bar(99.65, o=99.88, h=99.9, l=99.6, vol=120)
    b2 = _bar(99.9, o=100.02, h=100.1, l=99.85, vol=110)
    fillers = [_bar(100.05, o=100.05, h=100.15, l=99.95, vol=100) for _ in range(22)]
    bars = [trigger, b1, b2, *fillers]
    out = strat.evaluate(BarWindow(symbol="XAUUSD", timeframe="1min", bars=bars))
    assert len(out) == 1
    assert out[0].direction == "SHORT"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
