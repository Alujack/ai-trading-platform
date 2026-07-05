"""gold_news_fade — post-spike mean reversion (name kept for signal-history
continuity; this now fades *any* exhausted spike, not only news events).

Gold overreacts to sharp moves — a macro release (NFP, CPI, FOMC, PPI) is the
classic cause, but the strategy makes no attempt to detect an actual news
event: it fades any move > ``spike_min_atr`` × ATR that shows exhaustion.
Price typically overshoots, then partially retraces as the shock is absorbed;
this strategy fades the spike once evidence of exhaustion appears.

SETUP:
  - Trigger: AFTER a spike > ``spike_min_atr`` × ATR from the pre-spike close.
    The strategy does NOT trade the initial move — it waits for the move to
    exhaust.
  - Pre-condition: The spike must be genuine (large ATR-relative move), and
    a reversal candle must confirm exhaustion.

ENTRY (SHORT — fading an upward spike):
  1. Price is > ``spike_min_atr`` × ATR above the pre-spike close (the close
     ``lookback_pre`` bars ago, before the spike started)
  2. RSI(5) on fast timeframe > ``rsi_extreme`` (extreme overbought on fast RSI)
  3. A reversal candle appears: bearish candle with body > 50% of range
  → SHORT: SL = spike high + ``atr_buffer`` × ATR, TP = 50% retrace of the spike

ENTRY (LONG — fading a downward spike):
  Mirror conditions.

REGIME: All regimes.  On XAUUSD the classifier labels VOLATILE so rarely that a
        VOLATILE-only gate fired ~1 trade over 15k bars in backtest — opening it
        to all regimes is what makes the spike fade tradeable (and it survives
        walk-forward on 15min).

FREQUENCY: Moderate — fires on any exhausted ≥1×ATR spike, not just news windows.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import RANGING, TRENDING, VOLATILE, BarWindow, Drawing, IndicatorBar, SignalCandidate


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    key = f"gold_news_fade|{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


def _fast_rsi(bars: list[IndicatorBar], length: int = 5) -> Decimal | None:
    """Compute a fast RSI(5) from the most recent bars.

    Uses Wilder's Smoothing (RMA) on close prices. Returns None if
    insufficient data. Bars are most-recent first.
    """
    if len(bars) < length + 1:
        return None

    # We need a longer warmup for RMA, ideally at least 3x-4x length.
    # Since the strategy lookback is 60, we'll just use all available bars.
    closes = [float(b.close) for b in reversed(bars)]
    
    if len(closes) < length + 1:
        return None

    gains = []
    losses = []
    for i in range(1, length + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))

    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length

    for i in range(length + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(0, delta)
        loss = max(0, -delta)
        
        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length

    if avg_loss == 0:
        return Decimal("100")

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return Decimal(str(round(rsi, 2)))


class GoldNewsFade:
    name = "gold_news_fade"
    regimes = {TRENDING, RANGING, VOLATILE}  # allow news fades to trade in all regimes
    lookback = 60  # need pre-spike reference + the spike itself

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # Minimum spike size in ATR units to qualify as a fadeable spike.
        self.spike_min_atr = Decimal(str(p.get("spikeMinAtr", 1.0)))
        # How many bars back to find the "pre-spike" reference close.
        self.lookback_pre = int(p.get("lookbackPre", 12))
        # Fast RSI thresholds for exhaustion confirmation.
        self.rsi_extreme_high = Decimal(str(p.get("rsiExtremeHigh", 80)))
        self.rsi_extreme_low = Decimal(str(p.get("rsiExtremeLow", 20)))
        # Reversal candle body ratio (body / range must be > this).
        self.min_body_ratio = Decimal(str(p.get("minBodyRatio", 0.5)))
        # ATR buffer on the stop-loss beyond the spike extreme.
        self.atr_buffer = Decimal(str(p.get("atrBuffer", 0.5)))
        # Target: percentage of the spike to retrace.
        self.retrace_pct = Decimal(str(p.get("retracePct", 0.5)))
        self.min_rr = Decimal(str(p.get("minRr", 1.5)))
        self.ai_min_score = int(p.get("aiMinScore", 60))
        self.cooldown_ms = int(p.get("cooldownMs", 60 * 60 * 1000))  # 1 hour

    def _find_spike(
        self, bars: list[IndicatorBar], atr: Decimal
    ) -> tuple[str, Decimal, Decimal, Decimal, Decimal] | None:
        """Detect a recent spike by comparing current price to the pre-spike reference.

        Returns (spike_direction, pre_spike_close, spike_extreme, spike_size, spike_atr_ratio)
        or None if no qualifying spike found.
        """
        if len(bars) < self.lookback_pre + 2:
            return None

        # Pre-spike reference: the close N bars ago.
        pre_bar = bars[self.lookback_pre]
        pre_close = pre_bar.close
        current_close = bars[0].close

        # Find the extreme during the spike window.
        spike_bars = bars[:self.lookback_pre]
        spike_high = max((b.high for b in spike_bars if b.high is not None), default=current_close)
        spike_low = min((b.low for b in spike_bars if b.low is not None), default=current_close)

        # Upward spike: current area is far above pre-spike.
        up_size = spike_high - pre_close
        down_size = pre_close - spike_low

        if up_size > self.spike_min_atr * atr and up_size >= down_size:
            return ("UP", pre_close, spike_high, up_size, up_size / atr)
        elif down_size > self.spike_min_atr * atr:
            return ("DOWN", pre_close, spike_low, down_size, down_size / atr)

        return None

    def _is_reversal_candle(self, bar: IndicatorBar, direction: str) -> bool:
        """Check if the bar is a reversal candle against the spike direction."""
        if bar.open is None or bar.high is None or bar.low is None:
            return False

        body = abs(bar.close - bar.open)
        rng = bar.high - bar.low
        if rng <= Decimal("0"):
            return False

        body_ratio = body / rng
        if body_ratio < self.min_body_ratio:
            return False

        if direction == "UP":
            # Fading an up-spike: need a bearish candle (close < open).
            return bar.close < bar.open
        else:
            # Fading a down-spike: need a bullish candle (close > open).
            return bar.close > bar.open

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars  # most-recent first
        if len(bars) < self.lookback_pre + 5:
            return []

        latest = bars[0]
        if latest.atr is None or latest.atr <= Decimal("0"):
            return []
        if latest.open is None or latest.high is None or latest.low is None:
            return []

        atr = latest.atr
        close = latest.close

        # Detect spike.
        spike = self._find_spike(bars, atr)
        if spike is None:
            return []

        spike_dir, pre_close, spike_extreme, spike_size, spike_atr_ratio = spike

        # Compute fast RSI.
        fast_rsi = _fast_rsi(bars)
        if fast_rsi is None:
            return []

        # --- FADE an UP spike (SHORT entry) ---
        if spike_dir == "UP":
            if fast_rsi < self.rsi_extreme_high:
                return []
            if not self._is_reversal_candle(latest, "UP"):
                return []

            direction = "SHORT"
            stop = spike_extreme + self.atr_buffer * atr
            # Target = 50% retrace of the spike (calculated from the extreme).
            retrace_target = spike_extreme - (spike_size * self.retrace_pct)
            target = retrace_target

            risk = abs(stop - close)
            reward = abs(close - target)
            if risk <= Decimal("0") or (reward / risk) < self.min_rr:
                return []

            rr = reward / risk
            confidence = self._score(fast_rsi, spike_atr_ratio, rr, direction)

            reasoning = (
                f"[SPIKE FADE] SHORT @ {close}: Fading upward spike of {spike_size:.2f} "
                f"({float(spike_atr_ratio):.1f}×ATR) from pre-spike {pre_close}. "
                f"Spike high {spike_extreme}. Fast RSI(5)={fast_rsi} (exhausted). "
                f"Bearish reversal candle confirmed. "
                f"SL={stop:.2f} (spike high + {self.atr_buffer}×ATR), "
                f"TP={target:.2f} ({float(self.retrace_pct * 100)}% retrace). "
                f"RR 1:{float(rr):.1f}."
            )
            drawings = [
                Drawing(
                    type="hline",
                    coords=[Drawing._pt(None, pre_close)],
                    color="#6b7280",
                    label=f"Pre-spike {pre_close:.2f}",
                ),
                Drawing(
                    type="hline",
                    coords=[Drawing._pt(None, spike_extreme)],
                    color="#ef4444",
                    label=f"Spike High {spike_extreme:.2f}",
                ),
                Drawing(
                    type="hline",
                    coords=[Drawing._pt(None, target)],
                    color="#10b981",
                    label=f"Fade Target {target:.2f}",
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

        # --- FADE a DOWN spike (LONG entry) ---
        if spike_dir == "DOWN":
            if fast_rsi > self.rsi_extreme_low:
                return []
            if not self._is_reversal_candle(latest, "DOWN"):
                return []

            direction = "LONG"
            stop = spike_extreme - self.atr_buffer * atr
            # Target = 50% retrace of the spike (calculated from the extreme).
            retrace_target = spike_extreme + (spike_size * self.retrace_pct)
            target = retrace_target

            risk = abs(close - stop)
            reward = abs(target - close)
            if risk <= Decimal("0") or (reward / risk) < self.min_rr:
                return []

            rr = reward / risk
            confidence = self._score(fast_rsi, spike_atr_ratio, rr, direction)

            reasoning = (
                f"[SPIKE FADE] LONG @ {close}: Fading downward spike of {spike_size:.2f} "
                f"({float(spike_atr_ratio):.1f}×ATR) from pre-spike {pre_close}. "
                f"Spike low {spike_extreme}. Fast RSI(5)={fast_rsi} (exhausted). "
                f"Bullish reversal candle confirmed. "
                f"SL={stop:.2f} (spike low − {self.atr_buffer}×ATR), "
                f"TP={target:.2f} ({float(self.retrace_pct * 100)}% retrace). "
                f"RR 1:{float(rr):.1f}."
            )
            drawings = [
                Drawing(
                    type="hline",
                    coords=[Drawing._pt(None, pre_close)],
                    color="#6b7280",
                    label=f"Pre-spike {pre_close:.2f}",
                ),
                Drawing(
                    type="hline",
                    coords=[Drawing._pt(None, spike_extreme)],
                    color="#ef4444",
                    label=f"Spike Low {spike_extreme:.2f}",
                ),
                Drawing(
                    type="hline",
                    coords=[Drawing._pt(None, target)],
                    color="#10b981",
                    label=f"Fade Target {target:.2f}",
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
        self, fast_rsi: Decimal, spike_atr_ratio: Decimal, rr: Decimal, direction: str
    ) -> int:
        """Grade: more extreme RSI + larger spike + better RR = stronger fade setup."""
        score = 55

        # RSI extremity — further from neutral = more exhaustion.
        if direction == "SHORT":
            rsi_excess = fast_rsi - self.rsi_extreme_high
        else:
            rsi_excess = self.rsi_extreme_low - fast_rsi
        if rsi_excess > Decimal("10"):
            score += 12
        elif rsi_excess > Decimal("5"):
            score += 6

        # Spike magnitude — larger spikes retrace more reliably.
        if spike_atr_ratio > Decimal("4.0"):
            score += 10
        elif spike_atr_ratio > Decimal("3.0"):
            score += 7
        elif spike_atr_ratio > Decimal("2.0"):
            score += 3

        # RR bonus.
        if rr >= Decimal("2.5"):
            score += 8
        elif rr >= Decimal("2.0"):
            score += 4

        return max(50, min(90, score))
