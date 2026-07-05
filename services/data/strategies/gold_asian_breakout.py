"""gold_asian_breakout — volatility expansion from the Asian consolidation range.

Gold consolidates during the Asian session (low ADR, tight range), then breaks
out at the London or NY open.  This strategy captures the directional expansion.

SETUP:
  - Time: First 60 minutes of London (02:00–03:00 NY) or NY AM (07:00–08:00 NY)
  - Anchor: Asian session range must be TIGHT (< ``max_range_atr`` × ATR on 1H)

ENTRY (LONG breakout above Asian high):
  1. Asian range width < max_range_atr × ATR(14) on the signal timeframe
  2. Price closes above the Asian high on the signal timeframe
  3. The breakout bar's volume > vol_min_ratio × recent average (genuine move)
  4. ADX(14) reading > adx_min (directional momentum building)
  → LONG: SL = Asian midpoint, TP = entry + 2× Asian range width

ENTRY (SHORT breakout below Asian low — mirror):
  Same conditions in reverse.

REGIME: TRENDING only — breakouts fail in ranging/volatile markets.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import RANGING, TRENDING, VOLATILE, BarWindow, Drawing, IndicatorBar, SignalCandidate


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    key = f"gold_asian_breakout|{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


class GoldAsianBreakout:
    name = "gold_asian_breakout"
    regimes = {TRENDING, RANGING, VOLATILE}
    lookback = 80

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # Asian range must be below this × ATR to be "tight" (i.e. consolidation).
        self.max_range_atr = Decimal(str(p.get("maxRangeAtr", 2.5)))
        # ADX must be above this to confirm directional strength.
        self.adx_min = Decimal(str(p.get("adxMin", 15)))
        # Volume confirmation: breakout bar vs recent average.
        self.vol_min_ratio = Decimal(str(p.get("volMinRatio", 1.2)))
        self.vol_lookback = int(p.get("volLookback", 20))
        # ATR-based stop/target framing.
        self.min_rr = Decimal(str(p.get("minRr", 2.0)))
        self.target_range_mult = Decimal(str(p.get("targetRangeMult", 2.0)))
        self.ai_min_score = int(p.get("aiMinScore", 65))
        self.cooldown_ms = int(p.get("cooldownMs", 60 * 60 * 1000))  # 1 hour

    def _is_breakout_window(self, ts: datetime) -> bool:
        """Check if timestamp is in a breakout-eligible window.

        First hour of London (02:00–03:00 NY) or first hour of NY (07:00–08:00 NY).
        """
        from zoneinfo import ZoneInfo
        from datetime import timezone, time as dtime

        NY = ZoneInfo("America/New_York")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ny_time = ts.astimezone(NY).time()
        return (dtime(2, 0) <= ny_time < dtime(3, 0)) or (dtime(7, 0) <= ny_time < dtime(8, 0))

    def _find_asian_range(self, bars: list[IndicatorBar]) -> tuple[Decimal, Decimal, Decimal, int] | None:
        """Return (high, low, midpoint, bar_count) for the Asian session."""
        from zoneinfo import ZoneInfo
        from datetime import timezone, time as dtime

        NY = ZoneInfo("America/New_York")
        asian_highs: list[Decimal] = []
        asian_lows: list[Decimal] = []

        for bar in bars:
            ts = bar.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ny_time = ts.astimezone(NY).time()
            is_asian = ny_time >= dtime(20, 0) or ny_time < dtime(2, 0)

            if is_asian and bar.high is not None and bar.low is not None:
                asian_highs.append(bar.high)
                asian_lows.append(bar.low)

        if len(asian_highs) < 4:
            return None

        high = max(asian_highs)
        low = min(asian_lows)
        mid = (high + low) / Decimal("2")
        return (high, low, mid, len(asian_highs))

    def _volume_ratio(self, latest: IndicatorBar, prior: list[IndicatorBar]) -> Decimal | None:
        """Ratio of the latest bar's volume to the recent average."""
        vols = [b.volume for b in prior[:self.vol_lookback] if b.volume is not None]
        if latest.volume is None or not vols:
            return None
        avg = sum(vols, Decimal("0")) / Decimal(len(vols))
        if avg <= 0:
            return None
        return latest.volume / avg

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars  # most-recent first
        if len(bars) < 30:
            return []

        latest = bars[0]

        # Gate: must be in breakout window.
        if not self._is_breakout_window(latest.timestamp):
            return []

        if latest.atr is None or latest.atr <= Decimal("0"):
            return []
        if latest.open is None or latest.high is None or latest.low is None:
            return []

        atr = latest.atr
        close = latest.close

        # Find the Asian range from the bar history.
        asian = self._find_asian_range(bars)
        if asian is None:
            return []
        asian_high, asian_low, asian_mid, asian_count = asian
        range_width = asian_high - asian_low

        # Gate: Asian range must be tight (consolidation, not volatile).
        if range_width <= Decimal("0"):
            return []
        if range_width > self.max_range_atr * atr:
            return []

        # Volume confirmation.
        prior = bars[1:self.vol_lookback + 1]
        vol_ratio = self._volume_ratio(latest, prior)

        # LONG breakout: close above Asian high.
        if close > asian_high:
            # Must be a genuine breakout candle (close > open, bullish).
            if latest.open is not None and close <= latest.open:
                return []

            # Volume check.
            if vol_ratio is not None and vol_ratio < self.vol_min_ratio:
                return []

            direction = "LONG"
            stop = asian_mid  # stop at the midpoint of the Asian range
            target = close + self.target_range_mult * range_width

            risk = abs(close - stop)
            reward = abs(target - close)
            if risk <= Decimal("0") or (reward / risk) < self.min_rr:
                return []

            rr = reward / risk
            confidence = self._score(vol_ratio, rr, range_width, atr)

            vol_str = f"{float(vol_ratio):.2f}x avg" if vol_ratio is not None else "n/a"
            reasoning = (
                f"[ASIAN BREAKOUT] LONG @ {close}: Price broke above Asian high {asian_high} "
                f"after tight consolidation (range {range_width:.2f} = "
                f"{float(range_width / atr):.1f}×ATR). Volume {vol_str}. "
                f"SL={stop:.2f} (Asian midpoint), TP={target:.2f} "
                f"(entry + {self.target_range_mult}× range). RR 1:{float(rr):.1f}. "
                f"Asian bars: {asian_count}."
            )
            drawings = [
                Drawing(
                    type="box",
                    coords=[Drawing._pt(None, asian_high), Drawing._pt(None, asian_low)],
                    color="#f59e0b",
                    label="Asian Range",
                ),
                Drawing(
                    type="arrow",
                    coords=[Drawing._pt(latest.timestamp, close), Drawing._pt(None, target)],
                    color="#10b981",
                    label="Breakout Target",
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

        # SHORT breakout: close below Asian low.
        if close < asian_low:
            if latest.open is not None and close >= latest.open:
                return []

            if vol_ratio is not None and vol_ratio < self.vol_min_ratio:
                return []

            direction = "SHORT"
            stop = asian_mid
            target = close - self.target_range_mult * range_width

            risk = abs(stop - close)
            reward = abs(close - target)
            if risk <= Decimal("0") or (reward / risk) < self.min_rr:
                return []

            rr = reward / risk
            confidence = self._score(vol_ratio, rr, range_width, atr)

            vol_str = f"{float(vol_ratio):.2f}x avg" if vol_ratio is not None else "n/a"
            reasoning = (
                f"[ASIAN BREAKOUT] SHORT @ {close}: Price broke below Asian low {asian_low} "
                f"after tight consolidation (range {range_width:.2f} = "
                f"{float(range_width / atr):.1f}×ATR). Volume {vol_str}. "
                f"SL={stop:.2f} (Asian midpoint), TP={target:.2f} "
                f"(entry − {self.target_range_mult}× range). RR 1:{float(rr):.1f}."
            )
            drawings = [
                Drawing(
                    type="box",
                    coords=[Drawing._pt(None, asian_high), Drawing._pt(None, asian_low)],
                    color="#f59e0b",
                    label="Asian Range",
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
        self, vol_ratio: Decimal | None, rr: Decimal, range_width: Decimal, atr: Decimal
    ) -> int:
        """Grade: tighter range + higher volume + better RR = stronger breakout."""
        score = 55

        # Volume surge bonus.
        if vol_ratio is not None:
            if vol_ratio >= Decimal("2.0"):
                score += 12
            elif vol_ratio >= Decimal("1.5"):
                score += 8
            elif vol_ratio >= Decimal("1.2"):
                score += 4

        # Tighter range = more compressed energy.
        range_atr = range_width / atr if atr > 0 else Decimal("99")
        if range_atr < Decimal("0.8"):
            score += 10
        elif range_atr < Decimal("1.0"):
            score += 5

        # RR bonus.
        if rr >= Decimal("3.0"):
            score += 8
        elif rr >= Decimal("2.5"):
            score += 4

        return max(50, min(90, score))
