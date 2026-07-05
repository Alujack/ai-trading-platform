"""Unit tests for gold_zigzag_reversal (+ its higher-frequency `_daily` variant).

Hand-built OHLC fixtures in the runnable style of test_strategies.py / test_ict.py.
Covers: SHORT on a swing-high-then-reject, LONG on the mirror, the "don't chase"
entry-distance guard, the RR gate, the RSI-exhaustion guard, the requirement of a
prior opposing pivot for the target, payload/metadata shape, deterministic
client_id, and the registry wiring + tuned defaults of both variants.

Core-logic tests pass params explicitly so they stay valid if the tuned defaults
change; a separate block asserts the defaults themselves.

Run under pytest, or directly: ``python tests/test_gold_zigzag_reversal.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import BarWindow, IndicatorBar, build_strategy  # noqa: E402
from strategies.gold_zigzag_reversal import (  # noqa: E402
    GoldZigzagReversal,
    GoldZigzagReversalDaily,
)

TS = datetime(2026, 6, 23, 0, 0, 0)

# Params that make the core logic deterministic and independent of tuned defaults:
# depth 3 so the small fixtures below contain detectable pivots.
CORE = {"depth": 3, "entryMaxAtr": 1.0, "atrBuffer": 1.0, "minRr": 1.8}


def bar(i: int, h, l, c, atr: str = "1.0", rsi=None) -> IndicatorBar:
    """One 30-min XAU bar. open is unused by the strategy, so we mirror close."""
    return IndicatorBar(
        timestamp=TS + timedelta(minutes=30 * i),
        open=D(str(c)), high=D(str(h)), low=D(str(l)), close=D(str(c)),
        atr=D(atr),
        rsi=D(str(rsi)) if rsi is not None else None,
    )


def win(chrono: list[IndicatorBar], symbol: str = "XAUUSD", tf: str = "30min") -> BarWindow:
    """Build the most-recent-first window the engine hands to a strategy."""
    return BarWindow(symbol=symbol, timeframe=tf, bars=list(reversed(chrono)))


# ---- locked fixtures (validated against the detector) ---------------------- #
# Prior swing LOW at c3 (target) → climb → swing HIGH pivot at c10 (RSI 72) →
# price rejects back down, latest close 0.5 below the pivot high.
SHORT_FIX = [
    bar(0, 2001, 2000, 2000.5),
    bar(1, 2001.5, 2000.5, 2001),
    bar(2, 2002, 2001, 2001.5),
    bar(3, 1996, 1995, 1995.5, rsi=28),     # swing LOW  → target 1995
    bar(4, 1998, 1997, 1997.5),
    bar(5, 2000, 1999, 1999.5),
    bar(6, 2002, 2001, 2001.5),
    bar(7, 2004, 2003, 2003.5),
    bar(8, 2006, 2005, 2005.5),
    bar(9, 2008, 2007, 2007.5),
    bar(10, 2012, 2010, 2011, rsi=72),      # swing HIGH → SHORT pivot 2012
    bar(11, 2010, 2008, 2009),
    bar(12, 2009, 2007, 2008),
    bar(13, 2008, 2006, 2007),
    bar(14, 2011.8, 2010.5, 2011.5),        # latest: rejected 0.5 below pivot
]

# Mirror: prior swing HIGH at c3 (target) → decline → swing LOW pivot at c10
# (RSI 28) → price reclaims, latest close 0.8 above the pivot low.
LONG_FIX = [
    bar(0, 2001, 2000, 2000.5),
    bar(1, 2001.5, 2000.5, 2001),
    bar(2, 2002, 2001, 2001.5),
    bar(3, 2016, 2015, 2015.5, rsi=72),     # swing HIGH → target 2016
    bar(4, 2013, 2012, 2012.5),
    bar(5, 2011, 2010, 2010.5),
    bar(6, 2009, 2008, 2008.5),
    bar(7, 2007, 2006, 2006.5),
    bar(8, 2005, 2004, 2004.5),
    bar(9, 2003, 2002, 2002.5),
    bar(10, 2001, 1999, 1999.5, rsi=28),    # swing LOW → LONG pivot 1999
    bar(11, 2001.5, 2000, 2000.8),
    bar(12, 2002, 2000.5, 2001),
    bar(13, 2001.8, 2000.3, 2000.6),
    bar(14, 2000.2, 1999.4, 1999.8),        # latest: reclaimed 0.8 above pivot
]


def test_emits_short_on_swing_high_reject() -> None:
    out = build_strategy("gold_zigzag_reversal", CORE).evaluate(win(SHORT_FIX))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "SHORT"
    assert c.strategy_name == "gold_zigzag_reversal"
    assert c.entry == D("2011.5")
    assert c.stop == D("2012") + D("1.0") * D("1.0")   # pivot high + atrBuffer×ATR
    assert c.target == D("1995")                        # prior swing-low pivot
    assert c.stop > c.entry > c.target                  # geometry sane for a short


def test_emits_long_on_swing_low_reject() -> None:
    out = build_strategy("gold_zigzag_reversal", CORE).evaluate(win(LONG_FIX))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.entry == D("1999.8")
    assert c.stop == D("1999") - D("1.0") * D("1.0")    # pivot low − atrBuffer×ATR
    assert c.target == D("2016")                        # prior swing-high pivot
    assert c.stop < c.entry < c.target


def test_does_not_chase_when_price_ran_past_pivot() -> None:
    """Latest close > entryMaxAtr×ATR beyond the pivot → too late, no signal."""
    fix = LONG_FIX[:-1] + [bar(14, 2002.0, 2001.0, 2001.5)]  # 2.5 above pivot low
    assert build_strategy("gold_zigzag_reversal", CORE).evaluate(win(fix)) == []


def test_rr_gate_blocks_low_reward_setups() -> None:
    """Same valid SHORT geometry, but an impossibly high min RR filters it out."""
    strat = build_strategy("gold_zigzag_reversal", {**CORE, "minRr": 50})
    assert strat.evaluate(win(SHORT_FIX)) == []


def test_rsi_exhaustion_guard_blocks_short_without_overbought() -> None:
    """Swing-high pivot with a NON-overbought RSI reading → no reversal short."""
    fix = list(SHORT_FIX)
    fix[10] = bar(10, 2012, 2010, 2011, rsi=50)   # pivot no longer overbought
    assert build_strategy("gold_zigzag_reversal", CORE).evaluate(win(fix)) == []


def test_no_signal_without_prior_opposing_pivot() -> None:
    """A flat, featureless window has no pivots → nothing to trade."""
    flat = [bar(i, 2000.5, 1999.5, 2000) for i in range(20)]
    assert build_strategy("gold_zigzag_reversal", CORE).evaluate(win(flat)) == []


def test_payload_metadata_and_drawings() -> None:
    c = build_strategy("gold_zigzag_reversal", CORE).evaluate(win(SHORT_FIX))[0]
    p = c.to_payload()
    assert p["strategyName"] == "gold_zigzag_reversal"
    assert p["direction"] == "SHORT"
    assert isinstance(p["entryPrice"], float) and p["entryPrice"] == 2011.5
    assert p["cooldownMs"] == 30 * 60 * 1000
    assert p["aiMinScore"] == 65
    assert isinstance(c.client_id, str) and len(c.client_id) == 24
    assert 50 <= c.confidence <= 90
    assert len(c.drawings) == 3                          # zigzag leg + pivot + target
    assert p["drawings"][1]["type"] == "hline"


def test_client_id_is_deterministic() -> None:
    strat = build_strategy("gold_zigzag_reversal", CORE)
    a = strat.evaluate(win(SHORT_FIX))[0]
    b = strat.evaluate(win(SHORT_FIX))[0]
    assert a.client_id == b.client_id


# ---- registry wiring + tuned defaults ------------------------------------- #
def test_selective_defaults_and_regimes() -> None:
    s = build_strategy("gold_zigzag_reversal", None)
    assert isinstance(s, GoldZigzagReversal)
    assert s.name == "gold_zigzag_reversal"
    assert s.regimes == {"RANGING", "VOLATILE"}          # never trades TRENDING
    assert s.depth == 5 and s.entry_max_atr == D("0.5")  # sweep-optimised selective config


def test_daily_variant_defaults_and_regimes() -> None:
    s = build_strategy("gold_zigzag_reversal_daily", None)
    assert isinstance(s, GoldZigzagReversalDaily)
    assert isinstance(s, GoldZigzagReversal)              # shares the parent engine
    assert s.name == "gold_zigzag_reversal_daily"
    assert s.regimes == {"TRENDING", "RANGING", "VOLATILE"}
    # looser defaults → higher frequency than the selective parent
    assert s.depth == 3 and s.entry_max_atr == D("1.0") and s.min_rr == D("2.5")


def test_daily_variant_shares_core_logic() -> None:
    """With identical params, the daily subclass produces the same signal as the
    parent — it is a tuning, not a different engine."""
    parent = build_strategy("gold_zigzag_reversal", CORE).evaluate(win(SHORT_FIX))
    daily = build_strategy("gold_zigzag_reversal_daily", CORE).evaluate(win(SHORT_FIX))
    assert len(parent) == len(daily) == 1
    assert daily[0].direction == parent[0].direction == "SHORT"
    assert daily[0].entry == parent[0].entry
    assert daily[0].stop == parent[0].stop
    assert daily[0].target == parent[0].target


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
