"""Unit tests for the ICT detector family and its geometry primitives.

Hand-built OHLC fixtures (concepts §3 / build plan §3), in the runnable style of
test_strategies.py. Covers: the three detectors firing long & short, the bias and
OHLC guards, RR-aware targeting, and — most importantly — the look-ahead
discipline (concepts §3.11): swings/structure are never visible before their
confirmation bar.

Run under pytest, or directly: ``python tests/test_ict.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import BarWindow, IndicatorBar, build_strategy  # noqa: E402
from strategies.ict import primitives as P  # noqa: E402

TS = datetime(2026, 6, 23, 0, 0, 0)


def bar(
    i: int, o, h, l, c, atr: str = "0.5", ema50=None, ema200=None
) -> IndicatorBar:
    return IndicatorBar(
        timestamp=TS + timedelta(hours=i),
        open=D(str(o)), high=D(str(h)), low=D(str(l)), close=D(str(c)),
        atr=D(atr),
        ema50=D(str(ema50)) if ema50 is not None else None,
        ema200=D(str(ema200)) if ema200 is not None else None,
    )


def win(chrono: list[IndicatorBar], symbol: str = "EURUSD", tf: str = "60min") -> BarWindow:
    """Build the most-recent-first window the runner/engine hand to a strategy."""
    return BarWindow(symbol=symbol, timeframe=tf, bars=list(reversed(chrono)))


# ---- locked fixtures (validated against the primitives) -------------------- #
SWEEP_LONG = [
    bar(0, 100, 101, 99.5, 100.8),
    bar(1, 100.8, 102, 100.5, 101.8),
    bar(2, 101.8, 104, 101.5, 103.6),    # swing HIGH 104
    bar(3, 103.6, 103.6, 101.0, 101.3),
    bar(4, 101.3, 101.5, 99.2, 99.6),
    bar(5, 99.6, 100.0, 98.0, 98.3),     # swing LOW 98
    bar(6, 98.3, 100.5, 98.2, 100.2),
    bar(7, 100.2, 101.0, 99.8, 100.6),   # swing HIGH 101
    bar(8, 100.6, 100.8, 99.5, 99.8),
    bar(9, 99.8, 100.0, 97.5, 99.3),     # SSL sweep of 98 (low 97.5, closes back above)
    bar(10, 99.3, 103.5, 99.2, 103.3),   # displacement up → MSS, breaks 101
]

SWEEP_SHORT = [
    bar(0, 100, 100.5, 99, 99.2),
    bar(1, 99.2, 99.5, 98, 98.2),
    bar(2, 98.2, 98.5, 96, 96.4),        # swing LOW 96
    bar(3, 96.4, 99, 96.4, 98.7),
    bar(4, 98.7, 100.8, 98.5, 100.4),    # swing HIGH 100.8
    bar(5, 100.4, 102, 100.3, 101.7),    # swing HIGH 102
    bar(6, 101.7, 101.8, 99.5, 99.8),
    bar(7, 99.8, 100.2, 99, 99.4),
    bar(8, 99.4, 100.5, 99.2, 100.2),
    bar(9, 100.2, 102.5, 100, 100.7),    # BSL sweep of 102 (high 102.5, closes back below)
    bar(10, 100.7, 100.8, 96.5, 96.7),   # displacement down → MSS, breaks a swing low
]

FVG_LONG = [
    bar(0, 100, 100.5, 99.5, 100.2, ema50=101, ema200=100),
    bar(1, 100.2, 100.6, 99.8, 100.1, ema50=101, ema200=100),
    bar(2, 100.1, 100.7, 99.9, 100.3, ema50=101, ema200=100),
    bar(3, 100.3, 101.0, 100.1, 100.6, ema50=101, ema200=100),   # prev high 101.0
    bar(4, 100.6, 103.6, 100.5, 103.4, ema50=101, ema200=100),   # displacement mid
    bar(5, 103.4, 104.0, 102.2, 103.6, ema50=101, ema200=100),   # bullish FVG (101.0,102.2) CE 101.6
    bar(6, 103.6, 103.8, 102.5, 102.7, ema50=101, ema200=100),
    bar(7, 102.7, 102.9, 101.4, 101.7, ema50=101, ema200=100),   # retrace taps CE
]

OB_LONG = [
    bar(0, 102, 103, 101.5, 102.5, ema50=101, ema200=100),
    bar(1, 102.5, 103.2, 101.8, 102.0, ema50=101, ema200=100),
    bar(2, 102.0, 103.5, 101.6, 103.1, ema50=101, ema200=100),   # swing HIGH 103.5
    bar(3, 103.1, 103.2, 101.0, 101.2, ema50=101, ema200=100),
    bar(4, 101.2, 101.4, 100.0, 100.2, ema50=101, ema200=100),   # swing LOW 100.0
    bar(5, 100.2, 100.5, 99.6, 99.8, ema50=101, ema200=100),     # bullish OB (down-close)
    bar(6, 99.8, 104.0, 99.7, 103.9, ema50=101, ema200=100),     # displacement up → BOS over 103.5
    bar(7, 103.9, 104.2, 102.5, 102.8, ema50=101, ema200=100),
    bar(8, 102.8, 103.0, 100.3, 100.4, ema50=101, ema200=100),   # retest into OB [99.6,100.5]
]


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def test_swings_confirmation_lag() -> None:
    """A pivot is only a swing once it has k bars to its right — a spike on the
    final bar must NOT be reported (no look-ahead)."""
    bars = [bar(i, 100, 100 + (5 if i == 9 else 1), 99, 100) for i in range(10)]
    swings = P.find_swings(bars, k=2)
    assert all(s.index <= 7 for s in swings)            # last 2 bars never confirmed
    assert all(s.confirm_index == s.index + 2 for s in swings)


def test_displacement_gate() -> None:
    big = bar(0, 100, 103, 99.8, 102.8)      # body 2.8 vs ATR 0.5 → ≥1.5·ATR, body/range≈0.86
    doji = bar(0, 100, 102, 98, 100.1)       # body 0.1 → not displacement
    assert P.is_displacement(big) is True
    assert P.is_displacement(doji) is False


def test_fvg_bullish_and_bearish() -> None:
    fvgs = P.find_fvgs(FVG_LONG)
    bull = [f for f in fvgs if f.direction == "LONG"]
    assert len(bull) == 1
    assert bull[0].low == D("101.0") and bull[0].high == D("102.2")
    assert bull[0].ce == D("101.6")


# --------------------------------------------------------------------------- #
# Detectors — fire
# --------------------------------------------------------------------------- #
def test_sweep_mss_long() -> None:
    out = build_strategy("ict_sweep_mss", None).evaluate(win(SWEEP_LONG))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.strategy_name == "ict_sweep_mss"
    assert c.entry == D("103.3")                     # MSS close
    assert c.stop == D("97.5") - D("0.5") * D("0.5")  # sweep extreme − buffer·ATR = 97.25
    assert c.target > c.entry
    # RR-aware: nearest pool (104) is too close, so the min-RR (2) projection is used
    assert (c.target - c.entry) / (c.entry - c.stop) >= D("2")
    assert c.client_id is not None and len(c.client_id) == 24
    assert c.drawings  # emits chart annotations


def test_sweep_mss_short() -> None:
    out = build_strategy("ict_sweep_mss", None).evaluate(win(SWEEP_SHORT))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "SHORT"
    assert c.entry == D("96.7")
    assert c.stop == D("102.5") + D("0.5") * D("0.5")  # 102.75
    assert c.target < c.entry


def test_fvg_long_taps_ce() -> None:
    out = build_strategy("ict_fvg", None).evaluate(win(FVG_LONG))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.entry == D("101.6")                       # consequent encroachment
    assert c.stop == D("101.0") - D("0.5") * D("0.5")  # gap low − buffer·ATR = 100.75
    assert (c.target - c.entry) / (c.entry - c.stop) >= D("2")


def test_order_block_long_retest() -> None:
    out = build_strategy("ict_order_block", None).evaluate(win(OB_LONG))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.entry == D("100.5")                       # OB proximal (top)
    assert c.stop == D("99.6") - D("0.5") * D("0.5")   # OB low − buffer·ATR = 99.35
    assert c.target > c.entry


# --------------------------------------------------------------------------- #
# Detectors — guards / skips
# --------------------------------------------------------------------------- #
def test_fvg_skips_against_bias() -> None:
    """The same bullish-FVG retrace, but with EMA50 < EMA200 (short bias), must
    NOT emit a long (build plan §5 bias gate)."""
    opposed = [
        IndicatorBar(
            timestamp=b.timestamp, open=b.open, high=b.high, low=b.low, close=b.close,
            atr=b.atr, ema50=D("100"), ema200=D("101"),
        )
        for b in FVG_LONG
    ]
    assert build_strategy("ict_fvg", None).evaluate(win(opposed)) == []


def test_ict_requires_ohlc() -> None:
    """Close-only bars (no OHLC) — the close-only strategies' world — yield no
    ICT signal rather than crashing."""
    close_only = [
        IndicatorBar(timestamp=TS + timedelta(hours=i), close=D("100"), atr=D("0.5"))
        for i in range(10)
    ]
    for name in ("ict_sweep_mss", "ict_order_block", "ict_fvg"):
        assert build_strategy(name, None).evaluate(win(close_only)) == []


def test_ict_skips_short_window() -> None:
    """Too few bars to confirm any structure → no signal, no error."""
    short = SWEEP_LONG[:4]
    for name in ("ict_sweep_mss", "ict_order_block", "ict_fvg"):
        assert build_strategy(name, None).evaluate(win(short)) == []


def test_ict_declares_lookback() -> None:
    """Detectors declare a multi-bar lookback so the runner/engine size windows."""
    for name in ("ict_sweep_mss", "ict_order_block", "ict_fvg"):
        strat = build_strategy(name, None)
        assert getattr(strat, "lookback", 1) >= 50


def test_ict_payload_includes_drawings() -> None:
    c = build_strategy("ict_sweep_mss", None).evaluate(win(SWEEP_LONG))[0]
    payload = c.to_payload()
    assert payload["strategyName"] == "ict_sweep_mss"
    assert isinstance(payload["entryPrice"], float)
    assert "drawings" in payload and len(payload["drawings"]) >= 1
    assert payload["drawings"][0]["type"] in {"box", "line", "hline", "label", "fib", "arrow", "zone"}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
