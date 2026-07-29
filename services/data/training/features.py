"""Feature construction — imported by BOTH training and the live strategy.

This module exists because the ported `xaubot` model had two separate feature
implementations (a pandas one for training, a reimplementation for inference).
Any divergence between them is invisible and fatal: the model sees different
inputs live than it was fit on. Here there is exactly one code path, and it
takes the same `IndicatorBar` list the live `Strategy` protocol already hands us.

Two hard rules, both learned from the xaubot post-mortem:

1. **No absolute price levels.** `ema_10 = 1874.5` binds a tree model to the
   price regime it trained in — every split saturates once gold moves to $4000.
   Every price quantity here is divided by ATR or expressed as a ratio, so the
   feature distribution is stationary across price regimes.
2. **Nothing may look forward.** All ICT structure comes from
   `strategies/ict/primitives.py`, whose detectors carry an explicit confirmation
   lag (a swing is not visible until `confirm_index`, an order block not until
   its BOS). We pass `known_by=last` everywhere so a feature can only use what
   the decision bar could actually have seen.
"""
from __future__ import annotations

import math
from decimal import Decimal

import numpy as np

from regime import DEFAULT_ATR_BASELINE_WINDOW, RANGING, TRENDING, VOLATILE, classify
from sessions import classify_session
from strategies.base import IndicatorBar
from strategies.ict import killzones as KZ
from strategies.ict import primitives as P

# Bars of history each feature row needs. Driven by the regime ATR baseline
# (30) and the ICT detectors' structural lookback; 120 leaves headroom.
LOOKBACK = 120

FEATURE_NAMES: tuple[str, ...] = (
    # --- candle shape, ATR-normalised -------------------------------------
    "body_atr", "range_atr", "close_pos", "upper_wick_atr", "lower_wick_atr",
    # --- returns (already scale-free) -------------------------------------
    "ret_1", "ret_5", "ret_15", "ret_60",
    # --- volatility context -----------------------------------------------
    "atr_pct", "atr_expansion",
    # --- trend structure, ATR-normalised ----------------------------------
    "close_ema20_atr", "close_ema50_atr", "close_ema200_atr",
    "ema20_50_atr", "ema50_200_atr",
    # --- oscillators -------------------------------------------------------
    "rsi_n", "bb_pctb", "bb_width_atr", "adx_n",
    # --- ICT / SMC structure ----------------------------------------------
    "swing_high_dist_atr", "swing_low_dist_atr",
    "liq_above_dist_atr", "liq_below_dist_atr",
    "fvg_bull_present", "fvg_bull_dist_atr",
    "fvg_bear_present", "fvg_bear_dist_atr",
    "ob_bull_present", "ob_bull_dist_atr",
    "ob_bear_present", "ob_bear_dist_atr",
    "sweep_ssl", "sweep_bsl", "mss_up", "mss_down",
    "displacement", "ema_bias",
    # --- session / time ----------------------------------------------------
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "in_killzone", "sess_active", "sess_overlap",
    # --- regime one-hot ----------------------------------------------------
    "regime_trending", "regime_ranging", "regime_volatile",
)

N_FEATURES = len(FEATURE_NAMES)


def _f(x: Decimal | float | None) -> float:
    return float(x) if x is not None else math.nan


def _safe_div(a: float, b: float) -> float:
    return a / b if b and math.isfinite(b) and b != 0.0 else 0.0


def build_feature_row(bars: list[IndicatorBar], *, timeframe: str = "15min") -> np.ndarray | None:
    """One feature row for the LAST bar of a chronological `bars` window.

    Returns None when the window is too short or the decision bar lacks the
    OHLC/ATR it needs — callers skip those bars rather than imputing, since a
    fabricated feature row is worse than a missing one.
    """
    n = len(bars)
    if n < LOOKBACK:
        return None
    last = bars[-1]
    if not P.has_ohlc(last):
        return None
    atr = _f(last.atr)
    if not math.isfinite(atr) or atr <= 0:
        return None

    close = _f(last.close)
    high, low, open_ = _f(last.high), _f(last.low), _f(last.open)
    li = n - 1  # index of the decision bar

    v: dict[str, float] = {}

    # --- candle shape ------------------------------------------------------
    v["body_atr"] = (close - open_) / atr
    v["range_atr"] = (high - low) / atr
    v["close_pos"] = _safe_div(close - low, high - low)
    v["upper_wick_atr"] = (high - max(open_, close)) / atr
    v["lower_wick_atr"] = (min(open_, close) - low) / atr

    # --- returns -----------------------------------------------------------
    for k in (1, 5, 15, 60):
        prev = _f(bars[li - k].close) if li - k >= 0 else math.nan
        v[f"ret_{k}"] = _safe_div(close - prev, prev) if math.isfinite(prev) else 0.0

    # --- volatility --------------------------------------------------------
    v["atr_pct"] = _safe_div(atr, close)
    hist = [_f(b.atr) for b in bars[-DEFAULT_ATR_BASELINE_WINDOW - 1 : -1]]
    hist = [x for x in hist if math.isfinite(x) and x > 0]
    baseline = sum(hist) / len(hist) if hist else math.nan
    atr_expansion = _safe_div(atr, baseline) if math.isfinite(baseline) else 1.0
    v["atr_expansion"] = atr_expansion

    # --- trend structure ---------------------------------------------------
    e20, e50, e200 = _f(last.ema20), _f(last.ema50), _f(last.ema200)
    v["close_ema20_atr"] = (close - e20) / atr if math.isfinite(e20) else 0.0
    v["close_ema50_atr"] = (close - e50) / atr if math.isfinite(e50) else 0.0
    v["close_ema200_atr"] = (close - e200) / atr if math.isfinite(e200) else 0.0
    v["ema20_50_atr"] = (e20 - e50) / atr if math.isfinite(e20) and math.isfinite(e50) else 0.0
    v["ema50_200_atr"] = (e50 - e200) / atr if math.isfinite(e50) and math.isfinite(e200) else 0.0

    # --- oscillators -------------------------------------------------------
    rsi, pctb, adx = _f(last.rsi), _f(last.bb_pctb), _f(last.adx)
    bbu, bbl = _f(last.bb_upper), _f(last.bb_lower)
    v["rsi_n"] = rsi / 100.0 if math.isfinite(rsi) else 0.5
    v["bb_pctb"] = pctb if math.isfinite(pctb) else 0.5
    v["bb_width_atr"] = (bbu - bbl) / atr if math.isfinite(bbu) and math.isfinite(bbl) else 0.0
    v["adx_n"] = adx / 100.0 if math.isfinite(adx) else 0.0

    # --- ICT / SMC ---------------------------------------------------------
    # `known_by=li` everywhere: only structure confirmed by the decision bar.
    swings = P.find_swings(bars, k=2)
    close_d = last.close
    assert close_d is not None

    sh = P.last_swing(swings, "high", before_index=li, known_by=li)
    sl = P.last_swing(swings, "low", before_index=li, known_by=li)
    v["swing_high_dist_atr"] = (_f(sh.price) - close) / atr if sh else 0.0
    v["swing_low_dist_atr"] = (close - _f(sl.price)) / atr if sl else 0.0

    la = P.nearest_liquidity_above(swings, close_d, known_by=li)
    lb = P.nearest_liquidity_below(swings, close_d, known_by=li)
    v["liq_above_dist_atr"] = (_f(la.price) - close) / atr if la else 0.0
    v["liq_below_dist_atr"] = (close - _f(lb.price)) / atr if lb else 0.0

    # Nearest still-unmitigated FVG on each side, measured to its 50% level (CE).
    fvgs = P.find_fvgs(bars, require_displacement=True)
    for tag, want in (("bull", "LONG"), ("bear", "SHORT")):
        best = None
        for g in fvgs:
            if g.direction != want or g.index >= li:
                continue
            if not P.fvg_unmitigated_until(bars, g, li):
                continue
            if best is None or g.index > best.index:
                best = g
        v[f"fvg_{tag}_present"] = 1.0 if best else 0.0
        v[f"fvg_{tag}_dist_atr"] = (close - _f(best.ce)) / atr if best else 0.0

    obs = P.find_order_blocks(bars, swings)
    for tag, want in (("bull", "LONG"), ("bear", "SHORT")):
        best = None
        for ob in obs:
            if ob.direction != want or ob.bos_index > li:
                continue
            if not P.ob_unmitigated_until(bars, ob, li):
                continue
            if best is None or ob.index > best.index:
                best = ob
        v[f"ob_{tag}_present"] = 1.0 if best else 0.0
        v[f"ob_{tag}_dist_atr"] = (close - _f(best.proximal)) / atr if best else 0.0

    v["sweep_ssl"] = 1.0 if P.detect_sweep(bars, swings, end_index=li, side="SSL") else 0.0
    v["sweep_bsl"] = 1.0 if P.detect_sweep(bars, swings, end_index=li, side="BSL") else 0.0
    v["mss_up"] = 1.0 if P.mss_break(bars, swings, at_index=li, direction="LONG") else 0.0
    v["mss_down"] = 1.0 if P.mss_break(bars, swings, at_index=li, direction="SHORT") else 0.0

    disp = P.displacement_dir(last) if P.is_displacement(last) else None
    v["displacement"] = 1.0 if disp == "LONG" else (-1.0 if disp == "SHORT" else 0.0)
    bias = P.ema_bias(last)
    v["ema_bias"] = 1.0 if bias == "LONG" else (-1.0 if bias == "SHORT" else 0.0)

    # --- session / time ----------------------------------------------------
    ts = last.timestamp
    v["hour_sin"] = math.sin(2 * math.pi * ts.hour / 24.0)
    v["hour_cos"] = math.cos(2 * math.pi * ts.hour / 24.0)
    v["dow_sin"] = math.sin(2 * math.pi * ts.weekday() / 7.0)
    v["dow_cos"] = math.cos(2 * math.pi * ts.weekday() / 7.0)
    v["in_killzone"] = 1.0 if KZ.in_killzone(ts) else 0.0
    sess = classify_session(ts)
    v["sess_active"] = 1.0 if sess.is_active else 0.0
    v["sess_overlap"] = 1.0 if sess.is_overlap else 0.0

    # --- regime ------------------------------------------------------------
    reading = classify(
        adx if math.isfinite(adx) else None,
        atr_expansion if math.isfinite(atr_expansion) else None,
    )
    v["regime_trending"] = 1.0 if reading.regime == TRENDING else 0.0
    v["regime_ranging"] = 1.0 if reading.regime == RANGING else 0.0
    v["regime_volatile"] = 1.0 if reading.regime == VOLATILE else 0.0

    row = np.array([v[name] for name in FEATURE_NAMES], dtype=np.float32)
    return np.nan_to_num(row, nan=0.0, posinf=0.0, neginf=0.0)
