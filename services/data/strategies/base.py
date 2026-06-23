"""Strategy contract shared by every strategy module.

A strategy sees a `BarWindow` (recent bars + indicators, most-recent first) and
returns zero or more `SignalCandidate`s. It never touches the database or the
AI/risk gate directly — the runner POSTs candidates to the API gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

# Regime labels. Phase 5 computes the live regime; in Phase 4 these are only
# carried as metadata (each strategy declares which regimes it may trade).
TRENDING = "TRENDING"
RANGING = "RANGING"
VOLATILE = "VOLATILE"


@dataclass(slots=True)
class IndicatorBar:
    """One bar's close plus its indicator readings (any may be missing)."""

    timestamp: datetime
    close: Decimal
    rsi: Decimal | None = None
    ema20: Decimal | None = None
    ema50: Decimal | None = None
    ema200: Decimal | None = None
    atr: Decimal | None = None


@dataclass(slots=True)
class BarWindow:
    symbol: str
    timeframe: str
    bars: list[IndicatorBar]  # most-recent first

    @property
    def latest(self) -> IndicatorBar | None:
        return self.bars[0] if self.bars else None


@dataclass(slots=True)
class SignalCandidate:
    """A proposed trade, before AI + risk validation."""

    strategy_name: str
    symbol: str
    timeframe: str
    direction: str  # "LONG" | "SHORT"
    entry: Decimal
    stop: Decimal
    target: Decimal
    confidence: int
    reasoning: str
    client_id: str | None = None
    cooldown_ms: int | None = None
    ai_min_score: int | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize to the camelCase JSON the gate endpoint validates."""
        payload: dict[str, Any] = {
            "strategyName": self.strategy_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "entryPrice": float(self.entry),
            "stopLoss": float(self.stop),
            "takeProfit": float(self.target),
            "confidence": int(self.confidence),
            "reasoning": self.reasoning,
        }
        if self.client_id is not None:
            payload["clientId"] = self.client_id
        if self.cooldown_ms is not None:
            payload["cooldownMs"] = int(self.cooldown_ms)
        if self.ai_min_score is not None:
            payload["aiMinScore"] = int(self.ai_min_score)
        return payload


@runtime_checkable
class Strategy(Protocol):
    name: str
    regimes: set[str]

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]: ...
