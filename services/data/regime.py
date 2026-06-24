"""Regime classifier — the ADX/volatility gate the survey calls the highest-leverage feature.

`base.py` defines the regime labels (TRENDING / RANGING / VOLATILE) and each
strategy declares which regimes it may trade, but until now nothing computed the
*live* regime ("Phase 5 computes the live regime"). This module does that: it
reads a recent candle window and classifies it, so the runner can skip a strategy
whose declared regimes don't include the current one.

The classification follows the survey's framing
(`research/forex-strategy-survey.md`, §6.1):

  - Trend-following wins in strong directional moves; it whipsaws in chop.
  - Mean reversion wins in stable ranges; it blows up in trends and vol spikes.

So precedence is:

  ADX >= adx_trend                         -> TRENDING   (real directional strength;
                                                          expanding vol here is a *good*
                                                          trend, not a hazard)
  else, ATR spiking vs its baseline        -> VOLATILE   (directionless vol expansion —
                                                          news / whipsaw; the regime where
                                                          mean reversion fails worst)
  else                                     -> RANGING    (low ADX, stable vol — the clean
                                                          range mean reversion wants)

ADX requires ~2×length warm-up bars, so callers should pass a generous window
(see REGIME_LOOKBACK_BARS). The pure `classify()` takes the two scalars it needs
and is trivially unit-testable; `compute_regime()` derives them from a candle
window via pandas_ta_classic (already a dependency of indicator_calculator.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

import pandas as pd
import pandas_ta_classic as ta

from strategies.base import RANGING, TRENDING, VOLATILE

# Returned when there isn't enough data to classify (e.g. ADX hasn't warmed up).
# The runner treats UNKNOWN as "fail open": allow the strategy rather than
# silently halting trading on a thin window.
UNKNOWN = "UNKNOWN"

# ADX(14) needs ~28 bars to warm up; pull a comfortable margin so the latest
# reading is stable and the ATR baseline has history.
REGIME_LOOKBACK_BARS = 120

# Defaults are survey-aligned: ADX > 20 means a trend exists and > 40 is a strong
# trend, so 25 is a balanced "real trend" cut; the 20–25 grey zone is treated as
# range on purpose (don't let trend strategies fire on weak directionality).
DEFAULT_ADX_TREND = 25.0
DEFAULT_EXPANSION_MULT = 1.5  # latest ATR >= 1.5× its baseline counts as a spike
DEFAULT_ADX_LENGTH = 14
DEFAULT_ATR_LENGTH = 14
DEFAULT_ATR_BASELINE_WINDOW = 30  # bars of ATR averaged to form the "normal vol" baseline


@dataclass(slots=True)
class RegimeReading:
    """The classified regime plus the inputs that produced it (for logging/journaling)."""

    regime: str
    adx: float | None = None
    atr_expansion: float | None = None
    reason: str = ""


def classify(
    adx: float | None,
    atr_expansion: float | None,
    *,
    adx_trend: float = DEFAULT_ADX_TREND,
    expansion_mult: float = DEFAULT_EXPANSION_MULT,
) -> RegimeReading:
    """Pure classification from ADX and the ATR-expansion ratio.

    `atr_expansion` is latest ATR ÷ its rolling baseline (so 1.0 == normal vol,
    >1 == expanding). May be None when the baseline hasn't warmed up, in which
    case the VOLATILE check is skipped (we can't tell it's a spike).
    """
    if adx is None:
        return RegimeReading(UNKNOWN, adx, atr_expansion, "ADX unavailable (insufficient bars)")

    if adx >= adx_trend:
        return RegimeReading(
            TRENDING, adx, atr_expansion,
            f"ADX {adx:.1f} >= {adx_trend:g} — directional trend",
        )

    if atr_expansion is not None and atr_expansion >= expansion_mult:
        return RegimeReading(
            VOLATILE, adx, atr_expansion,
            f"ADX {adx:.1f} < {adx_trend:g} but ATR {atr_expansion:.2f}× baseline "
            f">= {expansion_mult:g} — directionless vol spike",
        )

    exp = "n/a" if atr_expansion is None else f"{atr_expansion:.2f}×"
    return RegimeReading(
        RANGING, adx, atr_expansion,
        f"ADX {adx:.1f} < {adx_trend:g} and vol stable (ATR {exp} baseline) — range",
    )


def _last_finite(series: pd.Series) -> float | None:
    s = series.dropna()
    if s.empty:
        return None
    val = float(s.iloc[-1])
    return val if pd.notna(val) else None


def compute_regime(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    adx_trend: float = DEFAULT_ADX_TREND,
    expansion_mult: float = DEFAULT_EXPANSION_MULT,
    adx_length: int = DEFAULT_ADX_LENGTH,
    atr_length: int = DEFAULT_ATR_LENGTH,
    atr_baseline_window: int = DEFAULT_ATR_BASELINE_WINDOW,
) -> RegimeReading:
    """Compute the regime from an ascending (oldest-first) candle window.

    Returns UNKNOWN if ADX can't be computed (too few bars). The ATR-expansion
    ratio is the latest ATR divided by the mean ATR over the prior
    `atr_baseline_window` bars; it's None until that baseline exists.
    """
    high = pd.Series([float(x) for x in highs], dtype="float64")
    low = pd.Series([float(x) for x in lows], dtype="float64")
    close = pd.Series([float(x) for x in closes], dtype="float64")

    if len(close) < adx_length + 2:
        return RegimeReading(UNKNOWN, None, None, "insufficient bars for ADX")

    adx_df = ta.adx(high, low, close, length=adx_length)
    adx_val: float | None = None
    if adx_df is not None and not adx_df.empty:
        adx_cols = adx_df.filter(like="ADX_")
        if not adx_cols.empty:
            adx_val = _last_finite(adx_cols.iloc[:, 0])

    atr = ta.atr(high, low, close, length=atr_length)
    atr_expansion: float | None = None
    if atr is not None:
        latest_atr = _last_finite(atr)
        # Baseline = mean ATR over the prior window, *excluding* the latest bar,
        # so a fresh spike is measured against the recent "normal", not itself.
        baseline_src = atr.dropna()
        if latest_atr is not None and len(baseline_src) > 1:
            baseline = baseline_src.iloc[:-1].tail(atr_baseline_window).mean()
            if pd.notna(baseline) and baseline > 0:
                atr_expansion = latest_atr / float(baseline)

    return classify(
        adx_val, atr_expansion, adx_trend=adx_trend, expansion_mult=expansion_mult,
    )


def gating_enabled() -> bool:
    """Whether the runner should gate strategies by regime (default: on).

    Set STRATEGY_REGIME_GATING=false to fall back to the pre-gate behavior
    (every enabled strategy runs in every regime).
    """
    return os.environ.get("STRATEGY_REGIME_GATING", "true").strip().lower() not in (
        "false", "0", "no", "off",
    )
