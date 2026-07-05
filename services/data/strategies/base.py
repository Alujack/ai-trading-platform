"""Strategy contract shared by every strategy module.

A strategy sees a `BarWindow` (recent bars + indicators, most-recent first) and
returns zero or more `SignalCandidate`s. It never touches the database or the
AI/risk gate directly — the runner POSTs candidates to the API gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    """One bar's close plus its indicator readings (any may be missing).

    `open`/`high`/`low` are optional so close-only strategies (and their test
    fixtures) keep working unchanged. Multi-bar price-action detectors (e.g. the
    ICT family) require the full OHLC and guard against `None` exactly as the
    indicator-based strategies guard against missing EMAs/RSI.
    """

    timestamp: datetime
    close: Decimal
    rsi: Decimal | None = None
    ema20: Decimal | None = None
    ema50: Decimal | None = None
    ema200: Decimal | None = None
    atr: Decimal | None = None
    bb_lower: Decimal | None = None
    bb_upper: Decimal | None = None
    bb_pctb: Decimal | None = None
    adx: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None


@dataclass(slots=True)
class Drawing:
    """A frontend-agnostic chart annotation a detector emits to show *what it saw*.

    Source-of-truth primitive (per the ICT build plan §4); the dashboard and the
    Telegram/Discord PNG renderer translate these into TradingView/mplfinance
    overlays. `coords` is a list of {"t": iso8601|None, "p": float} points — a box
    needs two corners, an hline needs one price (t=None), a line/arrow needs two
    endpoints, a fib needs the [from, to] anchors.
    """

    type: str  # "box" | "line" | "hline" | "label" | "fib" | "arrow" | "zone"
    coords: list[dict[str, Any]]
    color: str | None = None
    label: str | None = None

    @staticmethod
    def _pt(ts: datetime | None, price: Decimal) -> dict[str, Any]:
        return {"t": ts.isoformat() if ts is not None else None, "p": float(price)}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "coords": self.coords}
        if self.color is not None:
            out["color"] = self.color
        if self.label is not None:
            out["label"] = self.label
        return out


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
    drawings: list[Drawing] = field(default_factory=list)

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
        if self.drawings:
            payload["drawings"] = [d.to_dict() for d in self.drawings]
        return payload


@runtime_checkable
class Strategy(Protocol):
    name: str
    regimes: set[str]

    # Optional. How many trailing bars the strategy needs in its `BarWindow`.
    # Close-only strategies act on a single bar and omit it (treated as 1 by the
    # runner/backtest engine). Multi-bar price-action detectors (ICT) declare a
    # larger value, e.g. `lookback = 80`, so the window carries enough OHLC
    # history to confirm swings/structure. Read via getattr(strategy, "lookback", 1).
    # lookback: int

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]: ...
