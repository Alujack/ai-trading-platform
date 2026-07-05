"""gold_zigzag_reversal — swing-pivot reversal on XAU/USD (the "Happy Gold" core).

This is the *legitimate* engine behind the commercial "Happy Gold" EA, rebuilt
from first principles and stripped of the martingale/grid ("gridnews") behaviour
that the vendor's marketing quietly relies on. It does exactly ONE thing: enter a
mean-reversion trade when price prints a confirmed swing extreme (a ZigZag pivot)
and starts to reject away from it — one position, a hard ATR-scaled stop above the
pivot, and a structural target at the previous opposing pivot.

NB: no averaging-down, no re-entries, no hedging. If the reversal fails, the stop
takes the (bounded) loss. That is the deliberate opposite of the hidden-grid
design that produces Happy Gold's suspiciously smooth equity curve *and* its
account-blowup tail. Every candidate still passes through the API AI+risk gate
before it can become a live signal (CLAUDE.md).

SETUP:
  - A ZigZag pivot is a fractal swing: a bar whose high (low) exceeds the highs
    (lows) of ``depth`` bars on either side. A pivot is only *confirmed* once
    ``depth`` more-recent bars exist, so the newest usable pivot sits at index
    ``depth`` in the most-recent-first window.

ENTRY (SHORT after a swing-HIGH pivot):
  1. The most recent confirmed pivot is a swing high, formed within the last
     ``max_pivot_age`` bars.
  2. Price has rejected: latest close is back below the pivot high, but still
     within ``entry_max_atr`` × ATR of it (we are not chasing a move that already
     ran).
  3. If RSI is available, it was overbought (> ``rsi_ob``) at/near the pivot —
     momentum exhaustion.
  → SHORT: SL = pivot high + ``atr_buffer`` × ATR,
    TP = previous opposing (swing-low) pivot. Emitted only if RR ≥ ``min_rr``.

ENTRY (LONG after a swing-LOW pivot — mirror):
  Same logic in reverse; TP = previous opposing (swing-high) pivot.

REGIME: declares all three so the live/backtest regime gate decides. Reversal
        edges favour RANGING and post-spike VOLATILE; they degrade in strong
        TRENDING regimes (pivots keep getting broken), which the gate suppresses.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import RANGING, TRENDING, VOLATILE, BarWindow, Drawing, IndicatorBar, SignalCandidate


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    key = f"gold_zigzag_reversal|{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


class GoldZigzagReversal:
    name = "gold_zigzag_reversal"
    # Reversals fight strong trends (pivots keep breaking), so we do NOT trade the
    # TRENDING regime — the live/backtest regime gate suppresses those candidates.
    # A 72-config XAUUSD 15min sweep confirmed this: restricting to RANGING+VOLATILE
    # roughly doubled profit factor (1.19 → ~1.96) vs the all-regime version.
    regimes = {RANGING, VOLATILE}
    lookback = 80  # enough history to see two opposing pivots + confirmation bars

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # Defaults below are the sweep-optimised XAUUSD 15min config (depth 5,
        # tight entry, tight stop). Override per-symbol/timeframe via the Strategy
        # table params.
        # Fractal depth: bars required on EACH side of a bar for it to be a pivot.
        self.depth = int(p.get("depth", 5))
        # A tradable pivot must be no older than depth + this many bars.
        self.max_pivot_age = int(p.get("maxPivotAge", 4))
        # Entry must be within this × ATR of the pivot (don't chase).
        self.entry_max_atr = Decimal(str(p.get("entryMaxAtr", 0.5)))
        # ATR buffer added beyond the pivot extreme for the stop-loss.
        self.atr_buffer = Decimal(str(p.get("atrBuffer", 0.5)))
        # RSI exhaustion thresholds (only enforced when RSI is present).
        self.rsi_ob = Decimal(str(p.get("rsiOverbought", 60)))
        self.rsi_os = Decimal(str(p.get("rsiOversold", 40)))
        # Minimum RR to emit a signal.
        self.min_rr = Decimal(str(p.get("minRr", 1.8)))
        self.ai_min_score = int(p.get("aiMinScore", 65))
        self.cooldown_ms = int(p.get("cooldownMs", 30 * 60 * 1000))  # 30 min

    def _find_pivots(self, bars: list[IndicatorBar]) -> list[tuple[int, str, Decimal, datetime]]:
        """Detect confirmed fractal pivots.

        `bars` is most-recent-first. A pivot at index ``i`` needs ``depth`` bars on
        both the more-recent side (indices i-depth..i-1) and the older side
        (i+1..i+depth). Returns ``(index, "H"|"L", price, timestamp)`` ordered
        most-recent pivot first.
        """
        d = self.depth
        pivots: list[tuple[int, str, Decimal, datetime]] = []
        for i in range(d, len(bars) - d):
            bar = bars[i]
            if bar.high is None or bar.low is None:
                continue
            neighbors = bars[i - d:i] + bars[i + 1:i + d + 1]
            if any(n.high is None or n.low is None for n in neighbors):
                continue
            if all(bar.high > n.high for n in neighbors):  # type: ignore[operator]
                pivots.append((i, "H", bar.high, bar.timestamp))
            elif all(bar.low < n.low for n in neighbors):  # type: ignore[operator]
                pivots.append((i, "L", bar.low, bar.timestamp))
        return pivots

    def _rsi_near_pivot(self, bars: list[IndicatorBar], pivot_idx: int) -> Decimal | None:
        """RSI reading at the pivot bar, falling back to the nearest bar that has one."""
        for j in range(pivot_idx, min(pivot_idx + self.depth + 1, len(bars))):
            if bars[j].rsi is not None:
                return bars[j].rsi
        return None

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars  # most-recent first
        if len(bars) < self.depth * 2 + 5:
            return []

        latest = bars[0]
        if latest.atr is None or latest.atr <= Decimal("0"):
            return []
        if latest.high is None or latest.low is None:
            return []

        atr = latest.atr
        close = latest.close

        pivots = self._find_pivots(bars)
        if not pivots:
            return []

        pivot_idx, pivot_type, pivot_price, pivot_ts = pivots[0]

        # The pivot must be fresh — a stale swing is no longer actionable.
        if pivot_idx > self.depth + self.max_pivot_age:
            return []

        # Previous opposing pivot supplies the structural target.
        opposite = "L" if pivot_type == "H" else "H"
        prior = next((pv for pv in pivots[1:] if pv[1] == opposite), None)
        if prior is None:
            return []
        target = prior[2]

        rsi = self._rsi_near_pivot(bars, pivot_idx)

        if pivot_type == "H":
            # Swing high → SHORT reversal.
            distance = pivot_price - close
            if distance < Decimal("0"):
                return []  # price still above the pivot; no rejection yet
            if distance > self.entry_max_atr * atr:
                return []  # move already ran — too late to enter
            if rsi is not None and rsi < self.rsi_ob:
                return []  # no momentum exhaustion at the pivot
            if target >= close:
                return []  # target must sit below a short entry

            direction = "SHORT"
            stop = pivot_price + self.atr_buffer * atr
            risk = stop - close
            reward = close - target
        else:
            # Swing low → LONG reversal.
            distance = close - pivot_price
            if distance < Decimal("0"):
                return []
            if distance > self.entry_max_atr * atr:
                return []
            if rsi is not None and rsi > self.rsi_os:
                return []
            if target <= close:
                return []

            direction = "LONG"
            stop = pivot_price - self.atr_buffer * atr
            risk = close - stop
            reward = target - close

        if risk <= Decimal("0"):
            return []
        rr = reward / risk
        if rr < self.min_rr:
            return []

        leg_atr = abs(pivot_price - prior[2]) / atr
        confidence = self._score(rsi, rr, leg_atr, direction)

        rsi_str = f"{rsi}" if rsi is not None else "n/a"
        reasoning = (
            f"[ZIGZAG REVERSAL] {direction} @ {close}: confirmed swing "
            f"{'high' if pivot_type == 'H' else 'low'} pivot at {pivot_price} "
            f"({pivot_idx} bars back, depth {self.depth}); price rejected "
            f"{float(distance / atr):.2f}×ATR off it. RSI={rsi_str}. "
            f"SL={stop:.2f} (pivot {'+' if direction == 'SHORT' else '-'} "
            f"{self.atr_buffer}×ATR), TP={target:.2f} (prior "
            f"{'low' if opposite == 'L' else 'high'} pivot). "
            f"RR 1:{float(rr):.1f}. Last swing leg {float(leg_atr):.1f}×ATR."
        )
        drawings = [
            Drawing(
                type="line",
                coords=[Drawing._pt(prior[3], prior[2]), Drawing._pt(pivot_ts, pivot_price)],
                color="#a855f7",
                label="ZigZag leg",
            ),
            Drawing(
                type="hline",
                coords=[Drawing._pt(None, pivot_price)],
                color="#ef4444",
                label="Swing pivot",
            ),
            Drawing(
                type="arrow",
                coords=[Drawing._pt(latest.timestamp, close), Drawing._pt(None, target)],
                color="#10b981" if direction == "LONG" else "#ef4444",
                label="Reversal target",
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

    def _score(self, rsi: Decimal | None, rr: Decimal, leg_atr: Decimal, direction: str) -> int:
        """Grade: stronger RSI exhaustion + bigger swing leg + better RR = higher."""
        score = 55

        # RSI extremity bonus (further past the threshold = stronger exhaustion).
        if rsi is not None:
            excess = (rsi - self.rsi_ob) if direction == "SHORT" else (self.rsi_os - rsi)
            if excess > Decimal("15"):
                score += 10
            elif excess > Decimal("8"):
                score += 5

        # Larger prior swing leg = a more significant level being reversed.
        if leg_atr >= Decimal("4.0"):
            score += 10
        elif leg_atr >= Decimal("2.5"):
            score += 5

        # RR bonus.
        if rr >= Decimal("3.0"):
            score += 10
        elif rr >= Decimal("2.5"):
            score += 5

        return max(50, min(90, score))


class GoldZigzagReversalDaily(GoldZigzagReversal):
    """Higher-frequency ("trades most days") tuning of the ZigZag reversal.

    Same engine as the parent, loosened to fire ~1.2×/day on XAUUSD 15min (vs the
    selective parent's ~1 trade every 4 days): shallower pivots (depth 3), a wider
    entry window, and it trades ALL regimes (no regime gate suppression).

    This is a DELIBERATE frequency-for-edge trade. In-sample XAUUSD 15min:
        daily variant : ~1.24 trades/day, PF 1.27, +0.24R
        selective parent: ~0.25 trades/day, PF 1.96, +0.87R
    More action, thinner per-trade edge. Use when daily activity matters more than
    squeezing maximum expectancy per trade. Still one position at a time, hard ATR
    stop, RR-gated — no martingale/grid. Paper-trade before live (CLAUDE.md).
    """

    name = "gold_zigzag_reversal_daily"
    regimes = {TRENDING, RANGING, VOLATILE}

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = dict(params or {})
        p.setdefault("depth", 3)
        p.setdefault("entryMaxAtr", 1.0)
        p.setdefault("atrBuffer", 0.5)
        p.setdefault("minRr", 2.5)
        super().__init__(p)
