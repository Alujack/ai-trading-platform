"""meanrev_rsi — RSI extreme + EMA200 trend filter (migrated from strategy_detector.py).

LONG when RSI < 30 (oversold) and close > EMA200 (uptrend).
SHORT when RSI > 70 (overbought) and close < EMA200 (downtrend).
SL = 1.5·ATR, TP = 3·ATR (RR 1:2). Behavior held constant for Phase 4.

Each candidate carries a deterministic `client_id` (the same per-bar hash the
old detector used as the Signal id), so re-emitting the same bar is idempotent
at the gate — including bars the legacy CLI already inserted.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import RANGING, BarWindow, IndicatorBar, SignalCandidate


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    key = f"{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


class MeanRevRsi:
    name = "meanrev_rsi"
    regimes = {RANGING}

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.oversold = Decimal(str(p.get("rsiOversold", 30)))
        self.overbought = Decimal(str(p.get("rsiOverbought", 70)))
        self.atr_stop_mult = Decimal(str(p.get("atrStopMult", 1.5)))
        self.atr_target_mult = Decimal(str(p.get("atrTargetMult", 3)))
        self.ai_min_score = int(p.get("aiMinScore", 70))

    def _confidence(self, rsi: Decimal, direction: str) -> int:
        """Map distance from the RSI threshold to a 50–90 confidence score."""
        if direction == "LONG":
            ratio = max(Decimal("0"), self.oversold - rsi) / self.oversold
        else:
            ratio = max(Decimal("0"), rsi - self.overbought) / (Decimal("100") - self.overbought)
        score = 50 + int(min(Decimal("1"), ratio) * 40)
        return max(0, min(100, score))

    def _candidate(self, window: BarWindow, bar: IndicatorBar) -> SignalCandidate | None:
        if bar.rsi is None or bar.ema200 is None or bar.atr is None:
            return None
        rsi, ema200, atr, close = bar.rsi, bar.ema200, bar.atr, bar.close

        if rsi < self.oversold and close > ema200:
            direction = "LONG"
            stop = close - self.atr_stop_mult * atr
            target = close + self.atr_target_mult * atr
        elif rsi > self.overbought and close < ema200:
            direction = "SHORT"
            stop = close + self.atr_stop_mult * atr
            target = close - self.atr_target_mult * atr
        else:
            return None

        reasoning = (
            f"Bar {bar.timestamp.isoformat()}: RSI(14)={rsi} "
            f"{'< ' + str(self.oversold) + ' (oversold)' if direction == 'LONG' else '> ' + str(self.overbought) + ' (overbought)'} "
            f"and close={close} {'>' if direction == 'LONG' else '<'} EMA200={ema200}. "
            f"ATR(14)={atr}. SL = {self.atr_stop_mult}·ATR, TP = {self.atr_target_mult}·ATR (RR 1:2)."
        )
        return SignalCandidate(
            strategy_name=self.name,
            symbol=window.symbol,
            timeframe=window.timeframe,
            direction=direction,
            entry=close,
            stop=stop,
            target=target,
            confidence=self._confidence(rsi, direction),
            reasoning=reasoning,
            client_id=_signal_id(window.symbol, window.timeframe, direction, bar.timestamp),
            ai_min_score=self.ai_min_score,
        )

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        out: list[SignalCandidate] = []
        for bar in window.bars:
            cand = self._candidate(window, bar)
            if cand is not None:
                out.append(cand)
        return out
