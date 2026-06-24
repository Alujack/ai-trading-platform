"""Unit tests for the ICT confluence aggregator and killzone gating (build plan §5).

The aggregator only fires when several PD arrays stack in the bias direction
inside discount during a killzone — so the fixture is a full setup: an impulse
up-leg that leaves an OB + FVG, then a retrace that sweeps a minor low and taps
the stacked zone. Killzone timing is tested directly and via the gate.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import BarWindow, IndicatorBar, build_strategy  # noqa: E402
from strategies.ict import killzones as KZ  # noqa: E402

TS = datetime(2026, 6, 23)  # decision bar (idx14) → 14:00 UTC = 10:00 NY (EDT)


def bar(i, o, h, l, c, atr="0.5", ema50="103", ema200="100") -> IndicatorBar:
    return IndicatorBar(
        timestamp=TS + timedelta(hours=i),
        open=D(str(o)), high=D(str(h)), low=D(str(l)), close=D(str(c)), atr=D(atr),
        ema50=D(ema50) if ema50 else None, ema200=D(ema200) if ema200 else None,
    )


def win(chrono, symbol="EURUSD", tf="60min") -> BarWindow:
    return BarWindow(symbol=symbol, timeframe=tf, bars=list(reversed(chrono)))


# Locked fixture: sweep(SSL) + OB + FVG stack in discount on the decision bar.
CONF_LONG = [
    bar(0, 100, 100.8, 99.5, 100.5),
    bar(1, 100.5, 101.5, 100.3, 101.2),
    bar(2, 101.2, 102.5, 101.0, 102.0),   # swing HIGH 102.5
    bar(3, 102.0, 102.2, 100.0, 100.3),
    bar(4, 100.3, 100.5, 98.0, 98.3),     # swing LOW 98.0 (range bottom) / OB body
    bar(5, 98.3, 99.0, 98.2, 98.9),
    bar(6, 98.9, 99.1, 98.5, 98.6),       # OB down-close
    bar(7, 98.6, 103.0, 98.5, 102.8),     # displacement up → BOS over 102.5; FVG mid
    bar(8, 102.8, 106.0, 102.6, 105.6),   # displacement up; swing HIGH 106; FVG (99.1,102.6)
    bar(9, 105.6, 105.8, 104.0, 104.3),
    bar(10, 104.3, 104.5, 102.0, 102.3),  # retrace
    bar(11, 102.3, 102.6, 100.8, 101.0),  # minor swing LOW 100.8
    bar(12, 101.0, 101.3, 100.9, 101.1),
    bar(13, 101.1, 101.4, 101.0, 101.2),
    bar(14, 101.2, 101.5, 100.0, 101.0),  # DECISION: sweeps 100.8, taps FVG/OB, discount
]


# --------------------------------------------------------------------------- #
# Killzones
# --------------------------------------------------------------------------- #
def _utc(h: int) -> datetime:
    return datetime(2026, 6, 23, h, 0, tzinfo=timezone.utc)


def test_killzone_windows_ny_time() -> None:
    # EDT (UTC-4) on 2026-06-23: NY 08:00 = 12:00 UTC (ny_am); NY 03:00 = 07:00 UTC (london)
    assert KZ.active_killzone(_utc(12)) == "ny_am"
    assert KZ.active_killzone(_utc(7)) == "london"
    assert KZ.active_killzone(_utc(14)) == "silver_bullet"   # NY 10:00
    assert KZ.active_killzone(_utc(20)) is None              # NY 16:00, no window
    # end-exclusive: NY 10:00 is NOT ny_am
    assert KZ.active_killzone(_utc(14), ("ny_am",)) is None


def test_timeframe_is_intraday() -> None:
    assert KZ.timeframe_is_intraday("15min")
    assert KZ.timeframe_is_intraday("60min")
    assert not KZ.timeframe_is_intraday("daily")


# --------------------------------------------------------------------------- #
# Aggregator
# --------------------------------------------------------------------------- #
def test_confluence_fires_on_stacked_arrays() -> None:
    strat = build_strategy("ict_confluence", {"useKillzone": False})
    out = strat.evaluate(win(CONF_LONG))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.strategy_name == "ict_confluence"
    # three arrays agreed → score 0.70 → confidence 50 + 0.70*50 = 85
    assert c.confidence == 85
    assert "liquidity sweep" in c.reasoning and "OB" in c.reasoning and "FVG" in c.reasoning
    assert (c.target - c.entry) / (c.entry - c.stop) >= D("2")
    assert c.drawings


def test_confluence_killzone_gate() -> None:
    # decision bar is NY 10:00 → in silver_bullet, NOT in ny_am
    gated = build_strategy("ict_confluence", {"killzones": ["ny_am"]})
    assert gated.evaluate(win(CONF_LONG)) == []
    allowed = build_strategy("ict_confluence", {"killzones": ["silver_bullet"]})
    assert len(allowed.evaluate(win(CONF_LONG))) == 1


def test_confluence_daily_bypasses_killzone() -> None:
    """Daily bars have no intraday time, so the killzone gate must not apply."""
    strat = build_strategy("ict_confluence", None)  # killzone gate ON by default
    assert len(strat.evaluate(win(CONF_LONG, tf="daily"))) == 1


def test_confluence_requires_two_arrays() -> None:
    """A lone array (score below threshold) must not fire."""
    # Truncate so only the FVG forms (no sweep of a confirmed minor low, no retest
    # geometry): the single-FVG world scores 0.20 < 0.40.
    one_array = CONF_LONG[:9] + [bar(9, 105.6, 105.8, 100.7, 101.0)]
    strat = build_strategy("ict_confluence", {"useKillzone": False})
    out = strat.evaluate(win(one_array))
    assert out == [] or out[0].confidence < 70


def test_confluence_bias_gate() -> None:
    """Same stack, but EMA50 < EMA200 (short bias) blocks the long."""
    opposed = [
        IndicatorBar(
            timestamp=b.timestamp, open=b.open, high=b.high, low=b.low, close=b.close,
            atr=b.atr, ema50=D("100"), ema200=D("103"),
        )
        for b in CONF_LONG
    ]
    strat = build_strategy("ict_confluence", {"useKillzone": False})
    assert strat.evaluate(win(opposed)) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
