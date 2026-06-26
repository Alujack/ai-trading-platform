"""scalp_vwap — VWAP-anchored momentum scalp (the aggressive-scalper skill, survivable variant).

Encodes the manual scalping playbook's *entry brain* as a gated strategy. The
rules below are the ones that separated the winning trades from the bleed in live
testing:

  - **Trend-alignment GATE** — EMA20/EMA50 *and* price-vs-VWAP must agree, or no
    trade. (The skill: "trend alignment is the most common cause of losses.")
  - **LOCATION** — enter only on a pullback that retests VWAP and holds; never
    mid-range, never at the extended end of a move (the "don't chase a 6pt+ run"
    rule), and never within a band of the recent swing extreme (the "5pt from the
    session high/low" rule).
  - **CONFIRMATION** — require a confirmation candle that reclaims VWAP in the
    trend direction; never the price zone alone (the s6-long-1 lesson).
  - **VOLUME** — the confirmation bar's volume must hold vs its recent average.

It declares ``regimes = {TRENDING}`` so the runner's regime gate (``regime.py``)
skips it in RANGING (chop) and VOLATILE (news / whipsaw) — the regimes where every
variant of this strategy bled. That gate is the single most important safety here.

VWAP is computed from the bar window's OHLCV (typical = (H+L+C)/3, volume-weighted)
anchored over the whole lookback window, as an intraday-session approximation. A
future enhancement is to promote VWAP into the indicator pipeline so it is persisted
and anchored to the exact session open; for now it is self-contained here.

Stops are ATR-based and structural: SL = 1.5·ATR, TP = 3·ATR (RR 1:2), which clears
the risk engine's MIN_RR. The skill's close-when-blue / two-check-adverse exits are
*active management* and belong to the execution layer (Phase 3), not the entry signal.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import TRENDING, BarWindow, IndicatorBar, SignalCandidate


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    """Deterministic per-bar id, so re-emitting the same confirmation bar is
    idempotent at the gate (same scheme as meanrev_rsi)."""
    key = f"scalp_vwap|{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


def _vwap(bars: list[IndicatorBar]) -> Decimal | None:
    """Volume-weighted average of typical price over the window.

    Returns None if any bar lacks OHLC/volume or total volume is zero — VWAP is
    the strategy's bias anchor, so a partial computation is worse than abstaining.
    """
    num = Decimal("0")
    den = Decimal("0")
    for b in bars:
        if b.high is None or b.low is None or b.volume is None:
            return None
        typical = (b.high + b.low + b.close) / Decimal("3")
        num += typical * b.volume
        den += b.volume
    if den <= 0:
        return None
    return num / den


class ScalpVwap:
    name = "scalp_vwap"
    regimes = {TRENDING}  # gated OUT of RANGING (chop) and VOLATILE (news) by the runner
    lookback = 120  # ~2h of 1min bars: enough to anchor an intraday VWAP + read structure

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # Structural ATR frame — RR 1:2 clears the risk engine MIN_RR.
        self.atr_stop_mult = Decimal(str(p.get("atrStopMult", 1.5)))
        self.atr_target_mult = Decimal(str(p.get("atrTargetMult", 3)))
        # Location guards (all in ATR units, so they scale per-symbol/volatility):
        self.max_ext_atr = Decimal(str(p.get("maxExtAtr", 2.0)))      # skip if too far from VWAP (chasing)
        self.pullback_band_atr = Decimal(str(p.get("pullbackBandAtr", 0.75)))  # pullback must reach near VWAP
        self.extreme_band_atr = Decimal(str(p.get("extremeBandAtr", 1.0)))     # not within N·ATR of window hi/lo
        self.pullback_window = int(p.get("pullbackWindow", 6))        # bars to look back for the pullback
        self.vol_lookback = int(p.get("volLookback", 20))
        self.vol_min_ratio = Decimal(str(p.get("volMinRatio", 0.8)))  # confirmation bar's vol vs recent avg
        self.cooldown_ms = int(p.get("cooldownMs", 10 * 60 * 1000))   # modest: re-enter, don't spam
        self.ai_min_score = int(p.get("aiMinScore", 60))

    # -- factor helpers ---------------------------------------------------

    def _pullback_reached_vwap(
        self, prior: list[IndicatorBar], vwap: Decimal, atr: Decimal, direction: str
    ) -> tuple[bool, Decimal]:
        """Did price recently pull back to within `pullback_band_atr` of VWAP?

        Returns (reached, closeness) where closeness is the min distance to VWAP
        in ATR units over the pullback window (smaller = tighter retest = better).
        """
        band = self.pullback_band_atr * atr
        best = None
        for b in prior:
            if direction == "LONG":
                if b.low is None:
                    continue
                dist = b.low - vwap            # how far the pullback low sat above VWAP
            else:
                if b.high is None:
                    continue
                dist = vwap - b.high           # how far the bounce high sat below VWAP
            if best is None or dist < best:
                best = dist
        if best is None:
            return (False, Decimal("0"))
        reached = best <= band                  # pullback came within the band of VWAP
        closeness = (best / atr) if atr > 0 else Decimal("0")
        return (reached, closeness)

    def _volume_ratio(self, latest: IndicatorBar, prior: list[IndicatorBar]) -> Decimal | None:
        vols = [b.volume for b in prior[: self.vol_lookback] if b.volume is not None]
        if latest.volume is None or not vols:
            return None
        avg = sum(vols, Decimal("0")) / Decimal(len(vols))
        if avg <= 0:
            return None
        return latest.volume / avg

    # -- main -------------------------------------------------------------

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars  # most-recent first
        if len(bars) < max(self.pullback_window + 2, 20):
            return []

        latest = bars[0]
        if None in (latest.ema20, latest.ema50, latest.atr, latest.open, latest.high, latest.low):
            return []
        ema20, ema50, atr = latest.ema20, latest.ema50, latest.atr
        o, h, l, close = latest.open, latest.high, latest.low, latest.close
        assert ema20 is not None and ema50 is not None and atr is not None
        assert o is not None and h is not None and l is not None
        if atr <= 0:
            return []

        vwap = _vwap(bars)
        if vwap is None:
            return []

        prior = bars[1 : self.pullback_window + 1]   # bars before the confirmation bar
        window_high = max((b.high for b in bars if b.high is not None), default=close)
        window_low = min((b.low for b in bars if b.low is not None), default=close)

        # --- direction GATE: EMA stack AND price-vs-VWAP must agree ---
        long_trend = ema20 > ema50 and close > vwap
        short_trend = ema20 < ema50 and close < vwap
        if not long_trend and not short_trend:
            return []
        direction = "LONG" if long_trend else "SHORT"

        # --- LOCATION: not extended from VWAP (don't chase) ---
        if direction == "LONG":
            if (close - vwap) > self.max_ext_atr * atr:
                return []
            # not within extreme_band·ATR of the window high (the "5pt from high" rule)
            if (window_high - close) < self.extreme_band_atr * atr:
                return []
        else:
            if (vwap - close) > self.max_ext_atr * atr:
                return []
            if (close - window_low) < self.extreme_band_atr * atr:
                return []

        # --- LOCATION: a pullback must have retested VWAP recently ---
        reached, closeness = self._pullback_reached_vwap(prior, vwap, atr, direction)
        if not reached:
            return []

        # --- CONFIRMATION candle: reclaims VWAP in the trend direction ---
        if direction == "LONG":
            confirmed = close > o and close > vwap
            strong = bars[1].high is not None and close > bars[1].high
        else:
            confirmed = close < o and close < vwap
            strong = bars[1].low is not None and close < bars[1].low
        if not confirmed:
            return []

        # --- VOLUME: confirmation bar must hold vs recent average ---
        vol_ratio = self._volume_ratio(latest, prior)
        if vol_ratio is not None and vol_ratio < self.vol_min_ratio:
            return []

        # --- structural ATR frame (RR 1:2) ---
        if direction == "LONG":
            stop = close - self.atr_stop_mult * atr
            target = close + self.atr_target_mult * atr
        else:
            stop = close + self.atr_stop_mult * atr
            target = close - self.atr_target_mult * atr

        # --- grade -> confidence: tighter retest, holding volume, strong candle all add ---
        score = 55
        if closeness <= Decimal("0.3"):
            score += 11
        if vol_ratio is not None and vol_ratio >= Decimal("1.0"):
            score += 12
        if strong:
            score += 12
        confidence = max(50, min(90, score))
        grade = "A" if confidence >= 78 else "B" if confidence >= 64 else "C"

        reasoning = (
            f"[{grade}] {direction} scalp_vwap @ {close}: EMA20 {ema20} "
            f"{'>' if direction == 'LONG' else '<'} EMA50 {ema50} and price "
            f"{'above' if direction == 'LONG' else 'below'} VWAP {vwap:.3f} "
            f"(trend aligned). Pullback retested VWAP to {closeness:.2f}·ATR then a "
            f"confirmation candle reclaimed it"
            f"{' (engulfed prior bar)' if strong else ''}. "
            f"Volume {('n/a' if vol_ratio is None else f'{vol_ratio:.2f}x avg')}. "
            f"ATR {atr}. SL = {self.atr_stop_mult}·ATR, TP = {self.atr_target_mult}·ATR (RR 1:2). "
            f"Regime-gated to TRENDING only."
        )
        return [
            SignalCandidate(
                strategy_name=self.name,
                symbol=window.symbol,
                timeframe=window.timeframe,
                direction=direction,
                entry=close,
                stop=stop,
                target=target,
                confidence=confidence,
                reasoning=reasoning,
                client_id=_signal_id(window.symbol, window.timeframe, direction, latest.timestamp),
                cooldown_ms=self.cooldown_ms,
                ai_min_score=self.ai_min_score,
            )
        ]
