"""gold_london_sweep — ICT liquidity sweep of the Asian session range.

The highest-probability XAU/USD intraday setup.  Smart money sweeps the
Asian session high/low during the London killzone (02:00–05:00 NY) to grab
liquidity, then reverses.  This strategy detects the sweep-and-reject pattern.

SETUP:
  - Time: London killzone (02:00–05:00 NY)
  - Anchor: Asian session high and low (20:00–02:00 NY)

ENTRY (SHORT after BSL sweep):
  1. Price sweeps above the Asian high by > ``sweep_min_atr`` × ATR
  2. RSI(14) on the signal timeframe is overbought (> ``rsi_ob``)
  3. A rejection candle closes back below the Asian high
  → SHORT: SL = sweep extreme + atr_buffer × ATR, TP1 = Asian midpoint,
    TP2 = Asian low (or TP = Asian low if single-target mode)

ENTRY (LONG after SSL sweep — mirror):
  1. Price sweeps below the Asian low
  2. RSI oversold (< ``rsi_os``)
  3. Rejection candle closes back above the Asian low
  → LONG: SL = sweep extreme − atr_buffer × ATR, TP = Asian high

REGIME: TRENDING *and* RANGING (liquidity sweeps occur in both; only
        VOLATILE is excluded because vol spikes may prevent the rejection).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import RANGING, TRENDING, BarWindow, Drawing, IndicatorBar, SignalCandidate

# Import session tools — these are in the parent package.
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    key = f"gold_london_sweep|{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


class GoldLondonSweep:
    name = "gold_london_sweep"
    regimes = {TRENDING, RANGING}
    lookback = 80  # need enough bars to see the Asian range + London action

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # Sweep must exceed Asian level by at least this × ATR to count.
        self.sweep_min_atr = Decimal(str(p.get("sweepMinAtr", 0.3)))
        # RSI thresholds for confirmation.
        self.rsi_ob = Decimal(str(p.get("rsiOverbought", 70)))
        self.rsi_os = Decimal(str(p.get("rsiOversold", 30)))
        # ATR buffer added to the sweep extreme for the stop-loss.
        self.atr_buffer = Decimal(str(p.get("atrBuffer", 1.0)))
        # Minimum RR to emit a signal.
        self.min_rr = Decimal(str(p.get("minRr", 2.0)))
        self.ai_min_score = int(p.get("aiMinScore", 65))
        self.cooldown_ms = int(p.get("cooldownMs", 30 * 60 * 1000))  # 30 min

    def _find_asian_range(self, bars: list[IndicatorBar]) -> tuple[Decimal, Decimal, Decimal] | None:
        """Scan bars for the Asian session range (20:00–02:00 NY).

        Returns (asian_high, asian_low, midpoint) or None if insufficient data.
        The bars list is most-recent first, so we walk backwards into Asia.
        """
        from zoneinfo import ZoneInfo
        from datetime import timezone, time as dtime

        NY = ZoneInfo("America/New_York")
        asian_bars: list[IndicatorBar] = []

        for bar in bars:
            ts = bar.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ny_time = ts.astimezone(NY).time()

            # Asian session: 20:00–23:59 or 00:00–02:00
            is_asian = ny_time >= dtime(20, 0) or ny_time < dtime(2, 0)
            if is_asian and bar.high is not None and bar.low is not None:
                asian_bars.append(bar)

        if len(asian_bars) < 4:
            return None

        high = max(b.high for b in asian_bars)  # type: ignore[type-var]
        low = min(b.low for b in asian_bars)     # type: ignore[type-var]
        assert high is not None and low is not None
        mid = (high + low) / Decimal("2")
        return (high, low, mid)

    def _is_london(self, ts: datetime) -> bool:
        """Check if the timestamp is in the London killzone (02:00–05:00 NY)."""
        from zoneinfo import ZoneInfo
        from datetime import timezone, time as dtime

        NY = ZoneInfo("America/New_York")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ny_time = ts.astimezone(NY).time()
        return dtime(2, 0) <= ny_time < dtime(5, 0)

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars  # most-recent first
        if len(bars) < 20:
            return []

        latest = bars[0]
        if not self._is_london(latest.timestamp):
            return []

        if latest.high is None or latest.low is None or latest.open is None:
            return []
        if latest.atr is None or latest.atr <= Decimal("0"):
            return []
        if latest.rsi is None:
            return []

        atr = latest.atr
        close = latest.close
        rsi = latest.rsi

        asian = self._find_asian_range(bars)
        if asian is None:
            return []
        asian_high, asian_low, asian_mid = asian

        # Check for BSL sweep (bullish sweep above Asian high → SHORT setup)
        sweep_above = latest.high - asian_high  # type: ignore[operator]
        if (
            sweep_above is not None
            and sweep_above > self.sweep_min_atr * atr
            and close < asian_high       # closed back below = rejection
            and rsi > self.rsi_ob        # overbought confirmation
        ):
            direction = "SHORT"
            stop = latest.high + self.atr_buffer * atr  # type: ignore[operator]
            target = asian_low  # target the Asian low (full range reversal)

            risk = abs(stop - close)
            reward = abs(close - target)
            if risk <= Decimal("0") or (reward / risk) < self.min_rr:
                return []

            rr = reward / risk
            confidence = self._score(rsi, rr, sweep_above / atr, direction)

            reasoning = (
                f"[LONDON SWEEP] SHORT @ {close}: Price swept Asian high {asian_high} "
                f"by {sweep_above:.2f} ({float(sweep_above / atr):.1f}×ATR), then rejected "
                f"back below. RSI(14)={rsi} (overbought). "
                f"SL={stop:.2f} (sweep high + {self.atr_buffer}×ATR), "
                f"TP={target:.2f} (Asian low). RR 1:{float(rr):.1f}. "
                f"Asian range: {asian_high}–{asian_low} (mid {asian_mid:.2f})."
            )
            drawings = [
                Drawing(
                    type="box",
                    coords=[
                        Drawing._pt(None, asian_high),
                        Drawing._pt(None, asian_low),
                    ],
                    color="#4a90d9",
                    label="Asian Range",
                ),
                Drawing(
                    type="hline",
                    coords=[Drawing._pt(None, asian_mid)],
                    color="#6b7280",
                    label="Asian Mid",
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

        # Check for SSL sweep (bearish sweep below Asian low → LONG setup)
        sweep_below = asian_low - latest.low  # type: ignore[operator]
        if (
            sweep_below is not None
            and sweep_below > self.sweep_min_atr * atr
            and close > asian_low        # closed back above = rejection
            and rsi < self.rsi_os        # oversold confirmation
        ):
            direction = "LONG"
            stop = latest.low - self.atr_buffer * atr  # type: ignore[operator]
            target = asian_high  # target the Asian high

            risk = abs(close - stop)
            reward = abs(target - close)
            if risk <= Decimal("0") or (reward / risk) < self.min_rr:
                return []

            rr = reward / risk
            confidence = self._score(rsi, rr, sweep_below / atr, direction)

            reasoning = (
                f"[LONDON SWEEP] LONG @ {close}: Price swept Asian low {asian_low} "
                f"by {sweep_below:.2f} ({float(sweep_below / atr):.1f}×ATR), then reclaimed "
                f"back above. RSI(14)={rsi} (oversold). "
                f"SL={stop:.2f} (sweep low − {self.atr_buffer}×ATR), "
                f"TP={target:.2f} (Asian high). RR 1:{float(rr):.1f}. "
                f"Asian range: {asian_high}–{asian_low} (mid {asian_mid:.2f})."
            )
            drawings = [
                Drawing(
                    type="box",
                    coords=[
                        Drawing._pt(None, asian_high),
                        Drawing._pt(None, asian_low),
                    ],
                    color="#4a90d9",
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

    def _score(self, rsi: Decimal, rr: Decimal, sweep_depth_atr: Decimal, direction: str) -> int:
        """Grade the setup quality. Deeper sweeps + more extreme RSI + better RR = higher score."""
        score = 55

        # RSI extremity bonus (further from threshold = stronger)
        if direction == "SHORT":
            rsi_excess = rsi - self.rsi_ob
        else:
            rsi_excess = self.rsi_os - rsi
        if rsi_excess > Decimal("10"):
            score += 10
        elif rsi_excess > Decimal("5"):
            score += 5

        # Sweep depth bonus (deeper sweep = more liquidity grabbed)
        if sweep_depth_atr > Decimal("1.0"):
            score += 10
        elif sweep_depth_atr > Decimal("0.5"):
            score += 5

        # RR bonus
        if rr >= Decimal("3.0"):
            score += 10
        elif rr >= Decimal("2.5"):
            score += 5

        return max(50, min(90, score))
