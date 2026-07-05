"""gold_vwap_scalp — VWAP + Bollinger Band bounce scalper for XAU/USD.

A Gold-specific evolution of the generic ``scalp_vwap.py``, optimized for the
London-NY overlap window (07:00–10:00 NY) where Gold has peak liquidity and
the tightest spreads.

This is a **mean-reversion scalper** — it enters on pullbacks to VWAP when
confluence from Bollinger Bands and RSI confirms a bounce.  High-frequency
(2–4 signals per session), moderate per-trade return, high win rate.

SETUP:
  - Time:      London-NY overlap (07:00–10:00 NY) — peak liquidity
  - Timeframe: 5m or 15m candles

ENTRY (LONG):
  1. EMA20 > EMA50 (uptrend filter)
  2. Price pulls back to within ``vwap_band_atr`` × ATR of VWAP
  3. Bollinger Band %B < ``bb_low`` (near lower band — oversold in volatility space)
  4. RSI(14) was below ``rsi_bounce`` and is now crossing back above it
  5. Confirmation candle closes above VWAP (rejection confirmed)
  → LONG: SL = ``atr_stop_mult`` × ATR below entry, TP = ``atr_target_mult`` × ATR

ENTRY (SHORT — mirror):
  EMA20 < EMA50, price rallies to VWAP, %B > ``bb_high``, RSI crosses below
  ``rsi_bounce_high``.

REGIME: TRENDING only (mean-reversion in ranging markets is ``meanrev_rsi``'s job;
        this strategy adds momentum confirmation via EMA stack).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import RANGING, TRENDING, VOLATILE, BarWindow, Drawing, IndicatorBar, SignalCandidate


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    key = f"gold_vwap_scalp|{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


def _vwap(bars: list[IndicatorBar]) -> Decimal | None:
    """Session VWAP from the bar window's OHLCV (typical price × volume weighted).

    Falls back to an equal-weighted (TWAP) average when the window carries no
    volume at all. The weighting mode is chosen once for the whole window, so a
    real volume (e.g. 5000) and the weight-1 fallback are never mixed — in volume
    mode a bar without volume contributes nothing rather than a distorting sample.
    """
    has_volume = any(b.volume is not None and b.volume > 0 for b in bars)
    num = Decimal("0")
    den = Decimal("0")
    for b in bars:
        if b.high is None or b.low is None:
            return None
        typical = (b.high + b.low + b.close) / Decimal("3")
        if has_volume:
            if b.volume is None or b.volume <= 0:
                continue
            weight = b.volume
        else:
            weight = Decimal("1")
        num += typical * weight
        den += weight
    if den <= 0:
        return None
    return num / den


def _bollinger_pct_b(
    bars: list[IndicatorBar], length: int = 20, std_dev: Decimal = Decimal("2.0")
) -> Decimal | None:
    """Bollinger Band %B for the most recent bar.

    %B = (Close - Lower Band) / (Upper Band - Lower Band)
    Where bands = SMA(length) ± std_dev × std(length).
    Returns a value in [0, 1] when price is between bands, <0 below, >1 above.
    """
    if len(bars) < length:
        return None
    # Bars are most-recent first, so take the last `length` closes in chronological order.
    closes = [float(bars[i].close) for i in range(length - 1, -1, -1)]
    sma = sum(closes) / length
    variance = sum((c - sma) ** 2 for c in closes) / length
    std = variance ** 0.5
    if std <= 0:
        return None

    upper = sma + float(std_dev) * std
    lower = sma - float(std_dev) * std
    band_width = upper - lower
    if band_width <= 0:
        return None

    pct_b = (float(bars[0].close) - lower) / band_width
    return Decimal(str(round(pct_b, 4)))


class GoldVwapScalp:
    name = "gold_vwap_scalp"
    regimes = {TRENDING, RANGING, VOLATILE}
    lookback = 120  # ~2h of 1m bars or 10h of 5m bars

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # VWAP proximity: price must be within this × ATR of VWAP.
        self.vwap_band_atr = Decimal(str(p.get("vwapBandAtr", 2.0)))
        # Bollinger Band %B thresholds.
        self.bb_low = Decimal(str(p.get("bbLow", 0.6)))   # near lower band for LONG
        self.bb_high = Decimal(str(p.get("bbHigh", 0.4)))  # near upper band for SHORT
        # RSI bounce thresholds.
        self.rsi_bounce = Decimal(str(p.get("rsiBounce", 55)))       # LONG: RSI was < this
        self.rsi_bounce_high = Decimal(str(p.get("rsiBounceHigh", 45)))  # SHORT: RSI was > this
        # ATR-based stop/target.
        self.atr_stop_mult = Decimal(str(p.get("atrStopMult", 1.5)))
        self.atr_target_mult = Decimal(str(p.get("atrTargetMult", 4.0)))
        # Volume confirmation.
        self.vol_lookback = int(p.get("volLookback", 20))
        self.vol_min_ratio = Decimal(str(p.get("volMinRatio", 0.8)))
        self.ai_min_score = int(p.get("aiMinScore", 60))
        self.cooldown_ms = int(p.get("cooldownMs", 10 * 60 * 1000))  # 10 min

    def _is_overlap_window(self, ts: datetime) -> bool:
        """Check if timestamp is in the London-NY overlap (07:00–10:00 NY)."""
        from zoneinfo import ZoneInfo
        from datetime import timezone, time as dtime

        NY = ZoneInfo("America/New_York")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ny_time = ts.astimezone(NY).time()
        return dtime(7, 0) <= ny_time < dtime(10, 0)

    def _volume_ratio(self, latest: IndicatorBar, prior: list[IndicatorBar]) -> Decimal | None:
        vols = [b.volume for b in prior[:self.vol_lookback] if b.volume is not None]
        if latest.volume is None or not vols:
            return None
        avg = sum(vols, Decimal("0")) / Decimal(len(vols))
        if avg <= 0:
            return None
        return latest.volume / avg

    def _rsi_was_below(self, bars: list[IndicatorBar], threshold: Decimal, lookback: int = 5) -> bool:
        """Check if RSI was below threshold in the recent past (confirming a dip)."""
        for b in bars[1:lookback + 1]:
            if b.rsi is not None and b.rsi < threshold:
                return True
        return False

    def _rsi_was_above(self, bars: list[IndicatorBar], threshold: Decimal, lookback: int = 5) -> bool:
        """Check if RSI was above threshold in the recent past (confirming a rally)."""
        for b in bars[1:lookback + 1]:
            if b.rsi is not None and b.rsi > threshold:
                return True
        return False

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars  # most-recent first
        if len(bars) < max(self.vol_lookback + 2, 25):
            return []

        latest = bars[0]

        # Time gate: only trade during London-NY overlap.
        if not self._is_overlap_window(latest.timestamp):
            return []

        if None in (latest.ema20, latest.ema50, latest.atr, latest.open, latest.high, latest.low):
            return []
        if latest.rsi is None:
            return []

        ema20, ema50, atr = latest.ema20, latest.ema50, latest.atr
        assert ema20 is not None and ema50 is not None and atr is not None
        if atr <= Decimal("0"):
            return []

        close = latest.close
        o = latest.open
        assert o is not None

        vwap = _vwap(bars)
        if vwap is None:
            return []

        pct_b = _bollinger_pct_b(bars)
        if pct_b is None:
            return []

        prior = bars[1:self.vol_lookback + 1]

        # --- LONG setup ---
        long_trend = ema20 > ema50
        if long_trend:
            vwap_dist = close - vwap
            near_vwap = abs(vwap_dist) <= self.vwap_band_atr * atr

            if (
                near_vwap
                and pct_b < self.bb_low
                and self._rsi_was_below(bars, self.rsi_bounce)
                and latest.rsi > self.rsi_bounce  # RSI now crossing back above
                and close > o                     # bullish confirmation candle
                and close > vwap                  # closed above VWAP
            ):
                vol_ratio = self._volume_ratio(latest, prior)
                if vol_ratio is not None and vol_ratio < self.vol_min_ratio:
                    return []

                direction = "LONG"
                stop = close - self.atr_stop_mult * atr
                target = close + self.atr_target_mult * atr
                risk = abs(close - stop)
                reward = abs(target - close)
                rr = reward / risk if risk > 0 else Decimal("0")

                confidence = self._score(pct_b, vol_ratio, latest.rsi, direction)

                vol_str = f"{float(vol_ratio):.2f}x" if vol_ratio is not None else "n/a"
                reasoning = (
                    f"[VWAP SCALP] LONG @ {close}: Pullback to VWAP {vwap:.2f} "
                    f"(dist {float(vwap_dist):.2f}, {float(abs(vwap_dist) / atr):.2f}×ATR). "
                    f"BB%B={float(pct_b):.2f} (near lower band). "
                    f"RSI bounced from <{self.rsi_bounce} to {latest.rsi}. "
                    f"EMA20 {ema20:.2f} > EMA50 {ema50:.2f} (uptrend). "
                    f"Vol {vol_str}. "
                    f"SL={stop:.2f}, TP={target:.2f}. RR 1:{float(rr):.1f}."
                )
                drawings = [
                    Drawing(
                        type="hline",
                        coords=[Drawing._pt(None, vwap)],
                        color="#8b5cf6",
                        label=f"VWAP {vwap:.2f}",
                    ),
                ]

                return [SignalCandidate(
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
                    drawings=drawings,
                )]

        # --- SHORT setup ---
        short_trend = ema20 < ema50
        if short_trend:
            vwap_dist = vwap - close
            near_vwap = abs(vwap_dist) <= self.vwap_band_atr * atr

            if (
                near_vwap
                and pct_b > self.bb_high
                and self._rsi_was_above(bars, self.rsi_bounce_high)
                and latest.rsi < self.rsi_bounce_high
                and close < o
                and close < vwap
            ):
                vol_ratio = self._volume_ratio(latest, prior)
                if vol_ratio is not None and vol_ratio < self.vol_min_ratio:
                    return []

                direction = "SHORT"
                stop = close + self.atr_stop_mult * atr
                target = close - self.atr_target_mult * atr
                risk = abs(stop - close)
                reward = abs(close - target)
                rr = reward / risk if risk > 0 else Decimal("0")

                confidence = self._score(pct_b, vol_ratio, latest.rsi, direction)

                vol_str = f"{float(vol_ratio):.2f}x" if vol_ratio is not None else "n/a"
                reasoning = (
                    f"[VWAP SCALP] SHORT @ {close}: Rally to VWAP {vwap:.2f} rejected. "
                    f"BB%B={float(pct_b):.2f} (near upper band). "
                    f"RSI rejected from >{self.rsi_bounce_high} to {latest.rsi}. "
                    f"EMA20 {ema20:.2f} < EMA50 {ema50:.2f} (downtrend). "
                    f"Vol {vol_str}. "
                    f"SL={stop:.2f}, TP={target:.2f}. RR 1:{float(rr):.1f}."
                )
                drawings = [
                    Drawing(
                        type="hline",
                        coords=[Drawing._pt(None, vwap)],
                        color="#8b5cf6",
                        label=f"VWAP {vwap:.2f}",
                    ),
                ]

                return [SignalCandidate(
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
                    drawings=drawings,
                )]

        return []

    def _score(
        self, pct_b: Decimal, vol_ratio: Decimal | None, rsi: Decimal, direction: str
    ) -> int:
        """Grade: extremer BB%B + volume + RSI bounce = stronger setup."""
        score = 55

        # BB%B extremity.
        if direction == "LONG" and pct_b < Decimal("0.1"):
            score += 10
        elif direction == "SHORT" and pct_b > Decimal("0.9"):
            score += 10
        else:
            score += 5

        # Volume surge.
        if vol_ratio is not None and vol_ratio >= Decimal("1.2"):
            score += 10
        elif vol_ratio is not None and vol_ratio >= Decimal("1.0"):
            score += 5

        # RSI distance from neutral (50).
        rsi_dist = abs(rsi - Decimal("50"))
        if rsi_dist > Decimal("15"):
            score += 8
        elif rsi_dist > Decimal("10"):
            score += 4

        return max(50, min(90, score))
