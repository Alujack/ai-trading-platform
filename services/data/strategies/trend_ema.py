"""trend_ema — EMA-trend pullback (migrated from signalGenerator.ts).

LONG when EMA20 > EMA50 (bullish trend) and RSI is in a pullback band [40, 55]
and ATR exceeds a floor. SL = close − 1.5·ATR, TP = close + 3·ATR (RR 1:2).
Long-only and evaluates the latest bar only, exactly as the TS generator did;
behavior is held constant for Phase 4.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .base import TRENDING, BarWindow, SignalCandidate


class TrendEma:
    name = "trend_ema"
    regimes = {TRENDING}

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.rsi_min = Decimal(str(p.get("rsiMin", 40)))
        self.rsi_max = Decimal(str(p.get("rsiMax", 55)))
        self.atr_min = Decimal(str(p.get("atrMin", 5)))
        self.atr_stop_mult = Decimal(str(p.get("atrStopMult", 1.5)))
        self.atr_target_mult = Decimal(str(p.get("atrTargetMult", 3)))
        self.cooldown_ms = int(p.get("cooldownMs", 3_600_000))
        self.ai_min_score = int(p.get("aiMinScore", 70))

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bar = window.latest
        if bar is None:
            return []
        if None in (bar.ema20, bar.ema50, bar.rsi, bar.atr):
            return []

        ema20, ema50, rsi, atr = bar.ema20, bar.ema50, bar.rsi, bar.atr
        assert ema20 is not None and ema50 is not None and rsi is not None and atr is not None

        if not (ema20 > ema50):
            return []
        if not (self.rsi_min <= rsi <= self.rsi_max):
            return []
        if not (atr > self.atr_min):
            return []

        entry = bar.close
        stop = entry - self.atr_stop_mult * atr
        target = entry + self.atr_target_mult * atr
        reasoning = (
            f"EMA20 {ema20} > EMA50 {ema50} (bullish trend); "
            f"RSI {rsi} in [{self.rsi_min}, {self.rsi_max}] (pullback entry); "
            f"ATR {atr} > {self.atr_min} (sufficient volatility). "
            f"SL = close − {self.atr_stop_mult}·ATR, TP = close + {self.atr_target_mult}·ATR."
        )
        return [
            SignalCandidate(
                strategy_name=self.name,
                symbol=window.symbol,
                timeframe=window.timeframe,
                direction="LONG",
                entry=entry,
                stop=stop,
                target=target,
                confidence=0,
                reasoning=reasoning,
                cooldown_ms=self.cooldown_ms,
                ai_min_score=self.ai_min_score,
            )
        ]
