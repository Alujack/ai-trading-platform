"""Unit tests for ict_random_baseline — the geometry-matched random control.

The baseline must (a) honour the same killzone gate as ict_confluence, (b) place
a structural stop + ≥min_rr target with the same primitives, and (c) be
*reproducible per seed* (so a Monte-Carlo run means the same thing in every
process) while actually randomising direction across seeds. The fixture reuses
the confluence stack: it has confirmed swing highs (102.5, 106) and lows
(98.0, 100.8) around the decision close of 101.0, so both a LONG and a SHORT can
construct a valid stop/target.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import BarWindow, IndicatorBar, build_strategy  # noqa: E402

TS = datetime(2026, 6, 23)  # idx14 → 14:00 UTC = 10:00 NY (silver_bullet, NOT in default gate)


def bar(i, o, h, l, c, atr="0.5") -> IndicatorBar:
    return IndicatorBar(
        timestamp=TS + timedelta(hours=i),
        open=D(str(o)), high=D(str(h)), low=D(str(l)), close=D(str(c)), atr=D(atr),
    )


def win(chrono, symbol="EURUSD", tf="60min") -> BarWindow:
    return BarWindow(symbol=symbol, timeframe=tf, bars=list(reversed(chrono)))


# Swing highs 102.5 (idx2) & 106 (idx8); swing lows 98.0 (idx4) & 100.8 (idx11).
STRUCT = [
    bar(0, 100, 100.8, 99.5, 100.5),
    bar(1, 100.5, 101.5, 100.3, 101.2),
    bar(2, 101.2, 102.5, 101.0, 102.0),   # swing HIGH 102.5
    bar(3, 102.0, 102.2, 100.0, 100.3),
    bar(4, 100.3, 100.5, 98.0, 98.3),     # swing LOW 98.0
    bar(5, 98.3, 99.0, 98.2, 98.9),
    bar(6, 98.9, 99.1, 98.5, 98.6),
    bar(7, 98.6, 103.0, 98.5, 102.8),
    bar(8, 102.8, 106.0, 102.6, 105.6),   # swing HIGH 106
    bar(9, 105.6, 105.8, 104.0, 104.3),
    bar(10, 104.3, 104.5, 102.0, 102.3),
    bar(11, 102.3, 102.6, 100.8, 101.0),  # swing LOW 100.8
    bar(12, 101.0, 101.3, 100.9, 101.1),
    bar(13, 101.1, 101.4, 101.0, 101.2),
    bar(14, 101.2, 101.5, 100.0, 101.0),  # DECISION close 101.0
]

MIN_RR = D("2.0")
EPS = D("0.001")


def _first_seed(direction: str, lo: int = 0, hi: int = 64) -> int:
    """Smallest seed in [lo, hi) whose coin flip yields `direction` on STRUCT."""
    for s in range(lo, hi):
        out = build_strategy("ict_random_baseline", {"seed": s, "useKillzone": False}).evaluate(win(STRUCT))
        if out and out[0].direction == direction:
            return s
    raise AssertionError(f"no seed produced {direction} in [{lo},{hi})")


# --------------------------------------------------------------------------- #
# Killzone gate (identical contract to ict_confluence)
# --------------------------------------------------------------------------- #
def test_killzone_gate_blocks_outside_window() -> None:
    # idx14 = 10:00 NY = silver_bullet, NOT in default (london, ny_am) → gated out.
    strat = build_strategy("ict_random_baseline", {"seed": 1, "useKillzone": True})
    assert strat.evaluate(win(STRUCT)) == []


def test_killzone_disabled_allows_fire() -> None:
    strat = build_strategy("ict_random_baseline", {"seed": 1, "useKillzone": False})
    assert len(strat.evaluate(win(STRUCT))) == 1


def test_daily_timeframe_bypasses_killzone() -> None:
    strat = build_strategy("ict_random_baseline", {"seed": 1, "useKillzone": True})
    assert len(strat.evaluate(win(STRUCT, tf="1day"))) == 1


# --------------------------------------------------------------------------- #
# Reproducibility + actual randomness
# --------------------------------------------------------------------------- #
def test_same_seed_is_deterministic() -> None:
    a = build_strategy("ict_random_baseline", {"seed": 7, "useKillzone": False}).evaluate(win(STRUCT))
    b = build_strategy("ict_random_baseline", {"seed": 7, "useKillzone": False}).evaluate(win(STRUCT))
    assert a[0].direction == b[0].direction
    assert a[0].stop == b[0].stop and a[0].target == b[0].target


def test_seeds_produce_both_directions() -> None:
    dirs = {
        build_strategy("ict_random_baseline", {"seed": s, "useKillzone": False})
        .evaluate(win(STRUCT))[0].direction
        for s in range(32)
    }
    assert dirs == {"LONG", "SHORT"}  # the coin is actually flipping


# --------------------------------------------------------------------------- #
# Geometry matches the confluence frame (structural stop + ≥min_rr target)
# --------------------------------------------------------------------------- #
def test_long_geometry() -> None:
    c = build_strategy("ict_random_baseline", {"seed": _first_seed("LONG"), "useKillzone": False}).evaluate(win(STRUCT))[0]
    assert c.stop < c.entry < c.target
    reward, risk = c.target - c.entry, c.entry - c.stop
    assert reward / risk >= MIN_RR - EPS


def test_short_geometry() -> None:
    c = build_strategy("ict_random_baseline", {"seed": _first_seed("SHORT"), "useKillzone": False}).evaluate(win(STRUCT))[0]
    assert c.target < c.entry < c.stop
    reward, risk = c.entry - c.target, c.stop - c.entry
    assert reward / risk >= MIN_RR - EPS


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
def test_no_atr_returns_empty() -> None:
    bars = [bar(i, 101, 101.5, 100.5, 101, atr="0") for i in range(15)]
    assert build_strategy("ict_random_baseline", {"seed": 1, "useKillzone": False}).evaluate(win(bars)) == []


def test_too_few_bars_returns_empty() -> None:
    assert build_strategy("ict_random_baseline", {"seed": 1, "useKillzone": False}).evaluate(win(STRUCT[:5])) == []


def test_fire_prob_zero_never_fires() -> None:
    strat = build_strategy("ict_random_baseline", {"seed": 1, "useKillzone": False, "fireProb": 0.0})
    assert strat.evaluate(win(STRUCT)) == []
