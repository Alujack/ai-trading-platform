"""Unit tests for the regime classifier (the ADX/volatility strategy gate).

Mirrors the style of test_strategies.py. The pure `classify()` is tested with
explicit scalars (deterministic); `compute_regime()` gets a couple of smoke
tests on synthetic candle series. Runnable under pytest, or directly:
``python tests/test_regime.py``.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regime import (  # noqa: E402
    RANGING,
    TRENDING,
    UNKNOWN,
    VOLATILE,
    classify,
    compute_regime,
)


# --- classify(): the pure threshold logic ---------------------------------

def test_classify_trending_on_high_adx() -> None:
    # Strong ADX wins even when vol is also expanding — a real trend, not a hazard.
    r = classify(adx=30.0, atr_expansion=2.0)
    assert r.regime == TRENDING
    assert r.adx == 30.0


def test_classify_ranging_on_low_adx_stable_vol() -> None:
    r = classify(adx=15.0, atr_expansion=1.0)
    assert r.regime == RANGING


def test_classify_volatile_on_low_adx_vol_spike() -> None:
    # No trend (low ADX) but a sharp vol expansion -> directionless spike.
    r = classify(adx=15.0, atr_expansion=2.0)
    assert r.regime == VOLATILE


def test_classify_grey_zone_adx_is_ranging() -> None:
    # 20–25 is intentionally treated as range (below the 25 trend cut).
    r = classify(adx=22.0, atr_expansion=1.0)
    assert r.regime == RANGING


def test_classify_unknown_when_adx_missing() -> None:
    assert classify(adx=None, atr_expansion=1.0).regime == UNKNOWN


def test_classify_skips_volatile_when_expansion_unknown() -> None:
    # Can't call a spike without a baseline -> falls through to RANGING.
    r = classify(adx=15.0, atr_expansion=None)
    assert r.regime == RANGING


def test_classify_respects_custom_thresholds() -> None:
    assert classify(22.0, 1.0, adx_trend=20.0).regime == TRENDING
    assert classify(15.0, 1.3, expansion_mult=1.2).regime == VOLATILE


# --- compute_regime(): derives ADX/ATR from candle series -----------------

def _ascending_ramp(n: int = 80, start: float = 100.0, step: float = 1.0):
    """A clean monotonic uptrend -> should read as TRENDING (high ADX)."""
    highs, lows, closes = [], [], []
    for i in range(n):
        base = start + i * step
        highs.append(base + 0.5)
        lows.append(base - 0.5)
        closes.append(base)
    return highs, lows, closes


def _flat_oscillation(n: int = 80, mid: float = 100.0, amp: float = 0.5):
    """A tight range with no drift -> should read as RANGING (low ADX, stable vol).

    Each bar's range tracks its close so ATR/ADX are well-defined (constant
    highs/lows would make directional movement zero and ADX undefined).
    """
    highs, lows, closes = [], [], []
    for i in range(n):
        c = mid + amp * math.sin(i / 2.0)
        highs.append(c + 0.3)
        lows.append(c - 0.3)
        closes.append(c)
    return highs, lows, closes


def test_compute_regime_trending_on_ramp() -> None:
    r = compute_regime(*_ascending_ramp())
    assert r.regime == TRENDING
    assert r.adx is not None and r.adx >= 25.0


def test_compute_regime_ranging_on_flat() -> None:
    r = compute_regime(*_flat_oscillation())
    assert r.regime == RANGING


def test_compute_regime_unknown_on_short_window() -> None:
    highs, lows, closes = _ascending_ramp(n=5)
    assert compute_regime(highs, lows, closes).regime == UNKNOWN


def test_compute_regime_detects_atr_expansion_on_late_spike() -> None:
    # A calm range followed by a sudden range explosion must surface as a high
    # atr_expansion ratio — the input the VOLATILE branch keys on. (The branch
    # logic itself is covered deterministically by the classify() tests; ADX's
    # Wilder smoothing makes the final label too sensitive to fabricate here.)
    highs, lows, closes = _flat_oscillation(n=80)
    for i in range(-4, 0):
        mid = closes[i]
        highs[i] = mid + 8.0
        lows[i] = mid - 8.0
    r = compute_regime(highs, lows, closes)
    assert r.atr_expansion is not None and r.atr_expansion > 1.5


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
