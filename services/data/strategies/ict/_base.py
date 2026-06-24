"""Shared scaffolding for the ICT detectors.

Every ICT detector is a `Strategy`: it sees a multi-bar `BarWindow`
(most-recent-first) and returns at most one `SignalCandidate`. Common concerns —
flipping the window to chronological order, requiring full OHLC, RR-aware target
selection, deterministic idempotency ids, and confidence scoring — live here so
the individual detectors stay focused on their pattern logic.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..base import TRENDING, RANGING, VOLATILE, BarWindow, IndicatorBar
from . import primitives as P

ZERO = Decimal("0")


@dataclass(slots=True)
class TargetPlan:
    target: Decimal
    rr: Decimal
    source: str  # "liquidity" (next opposing pool) | "rr" (min-RR fallback)


def resolve_target(
    direction: str,
    entry: Decimal,
    stop: Decimal,
    liquidity: Decimal | None,
    min_rr: Decimal,
) -> TargetPlan | None:
    """Target the next opposing liquidity pool when it clears the min RR, else
    fall back to a min-RR projection (build plan §6: take the liquidity target,
    but never propose a sub-min-RR trade). Returns None on a degenerate stop."""
    risk = abs(entry - stop)
    if risk <= ZERO:
        return None
    if direction == "LONG":
        liq_ok = liquidity is not None and liquidity > entry and (liquidity - entry) / risk >= min_rr
        target = liquidity if liq_ok else entry + min_rr * risk  # type: ignore[operator]
    else:
        liq_ok = liquidity is not None and liquidity < entry and (entry - liquidity) / risk >= min_rr
        target = liquidity if liq_ok else entry - min_rr * risk  # type: ignore[operator]
    rr = abs(target - entry) / risk
    return TargetPlan(target=target, rr=rr, source="liquidity" if liq_ok else "rr")


def confidence_from_rr(rr: Decimal, base: int, min_rr: Decimal) -> int:
    """Map realised RR above the floor to a base..90 confidence band."""
    bump = int(max(ZERO, (rr - min_rr)) * Decimal("12"))
    return max(0, min(90, base + bump))


def signal_id(symbol: str, timeframe: str, name: str, direction: str, ts: datetime) -> str:
    key = f"{name}|{symbol}|{timeframe}|{direction}|{ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


class IctBase:
    """Base for the ICT detectors. Subclasses set ``name`` and a ``base_confidence``
    and implement ``_evaluate(chrono, window)``."""

    name: str = "ict_base"
    regimes: set[str] = {TRENDING, RANGING, VOLATILE}
    base_confidence: int = 55
    default_lookback: int = 80

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.swing_k = int(p.get("swingK", 2))
        self.atr_buffer = Decimal(str(p.get("atrBuffer", "0.5")))
        self.min_rr = Decimal(str(p.get("minRr", "2.0")))
        self.sweep_lookback = int(p.get("sweepLookback", 5))
        self.cooldown_ms = int(p.get("cooldownMs", 3_600_000))
        self.ai_min_score = int(p.get("aiMinScore", 70))
        self.lookback = int(p.get("lookback", self.default_lookback))

    # subclasses override
    def _evaluate(self, chrono: list[IndicatorBar], window: BarWindow):  # noqa: ANN201
        raise NotImplementedError

    def evaluate(self, window: BarWindow):  # noqa: ANN201
        # Window arrives most-recent-first; ICT geometry is far easier to reason
        # about oldest-first, with chrono[-1] == the just-closed decision bar.
        chrono = list(reversed(window.bars))
        if len(chrono) < (2 * self.swing_k + 3):
            return []
        if not P.window_has_ohlc(chrono):
            return []
        if chrono[-1].atr is None or chrono[-1].atr <= ZERO:
            return []
        return self._evaluate(chrono, window)
