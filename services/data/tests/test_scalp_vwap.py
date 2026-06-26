"""Unit tests for scalp_vwap, on hand-built OHLCV fixtures.

Mirrors test_strategies.py. Runnable under pytest, or directly:
``python tests/test_scalp_vwap.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import BarWindow, IndicatorBar, TRENDING, build_strategy  # noqa: E402
from strategies.scalp_vwap import ScalpVwap  # noqa: E402

TS = datetime(2026, 6, 26, 12, 0, 0)


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


def _long_window(*, latest: IndicatorBar | None = None) -> BarWindow:
    """An uptrend that pulled back to VWAP (~100.05) and printed a bullish
    confirmation candle at 100.8. `latest` overrides the confirmation bar for
    negative cases."""
    confirm = latest or _bar(
        100.8, o=100.2, h=101.0, l=100.1, vol=130, ema20=100.6, ema50=100.0, atr=0.5
    )
    pullback = _bar(100.2, o=100.5, h=100.6, l=99.95, vol=100)   # low retests VWAP
    swing_high = _bar(100.0, o=100.0, h=101.8, l=99.5, vol=100)  # gives a window high above close
    fillers = [_bar(100.0, o=100.0, h=100.2, l=99.85, vol=100) for _ in range(21)]
    bars = [confirm, pullback, swing_high, *fillers]  # most-recent first; 24 bars
    return BarWindow(symbol="XAUUSD", timeframe="1min", bars=bars)


def test_scalp_vwap_emits_long_on_vwap_pullback_with_confirmation() -> None:
    strat = build_strategy("scalp_vwap", None)
    out = strat.evaluate(_long_window())
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.strategy_name == "scalp_vwap"
    assert c.entry == Decimal("100.8")
    assert c.stop == Decimal("100.8") - Decimal("1.5") * Decimal("0.5")   # 100.05
    assert c.target == Decimal("100.8") + Decimal("3") * Decimal("0.5")   # 102.3
    assert 50 <= c.confidence <= 90
    assert c.client_id is not None and len(c.client_id) == 24


def test_scalp_vwap_skips_when_trend_not_aligned() -> None:
    # EMA stack flipped bearish while price is still above VWAP -> neither side aligns.
    strat = build_strategy("scalp_vwap", None)
    latest = _bar(100.8, o=100.2, h=101.0, l=100.1, vol=130, ema20=100.0, ema50=100.6, atr=0.5)
    assert strat.evaluate(_long_window(latest=latest)) == []


def test_scalp_vwap_skips_when_extended_from_vwap() -> None:
    # Same setup but tiny ATR -> close sits > max_ext·ATR above VWAP (chasing).
    strat = build_strategy("scalp_vwap", None)
    latest = _bar(100.8, o=100.2, h=101.0, l=100.1, vol=130, ema20=100.6, ema50=100.0, atr=0.2)
    assert strat.evaluate(_long_window(latest=latest)) == []


def test_scalp_vwap_skips_without_confirmation_candle() -> None:
    # Bearish confirmation bar (close < open) in an uptrend -> no entry on the zone alone.
    strat = build_strategy("scalp_vwap", None)
    latest = _bar(100.8, o=101.0, h=101.1, l=100.1, vol=130, ema20=100.6, ema50=100.0, atr=0.5)
    assert strat.evaluate(_long_window(latest=latest)) == []


def test_scalp_vwap_skips_on_thin_window() -> None:
    strat = build_strategy("scalp_vwap", None)
    bars = [_bar(100.8, o=100.2, h=101.0, l=100.1, ema20=100.6, ema50=100.0, atr=0.5)]
    assert strat.evaluate(BarWindow(symbol="XAUUSD", timeframe="1min", bars=bars)) == []


def test_scalp_vwap_is_trending_only() -> None:
    # The regime gate (runner) keeps this out of chop/news; assert the declaration.
    assert ScalpVwap.regimes == {TRENDING}


def test_scalp_vwap_payload_is_camelcase_json() -> None:
    strat = build_strategy("scalp_vwap", None)
    payload = strat.evaluate(_long_window())[0].to_payload()
    assert payload["strategyName"] == "scalp_vwap"
    assert payload["direction"] == "LONG"
    assert isinstance(payload["entryPrice"], float) and payload["entryPrice"] == 100.8
    assert payload["cooldownMs"] == 10 * 60 * 1000
    assert "clientId" in payload


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
