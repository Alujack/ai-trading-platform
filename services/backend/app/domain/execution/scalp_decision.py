"""Pure decision core for the fast scalp manager — port of `execution/scalpDecision.ts`.

No I/O, so it is unit-tested in isolation the same way the risk engine and
`evaluate_exit` keep their decision logic pure. Everything is in
R = unrealized profit ÷ the trade's risk amount, so thresholds are
scale-invariant across symbols and account sizes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...core.settings import get_settings


@dataclass(slots=True)
class ScalpManageConfig:
    #: First-check close if actualStopDist / intendedStopDist < this.
    minStopRatio: float
    #: One-check emergency close at R <= -emergencyR.
    emergencyR: float
    #: Two-check adverse engages once R <= -watchR.
    watchR: float
    #: Profit-lock arms once best-seen R >= trailStartR.
    trailStartR: float
    #: Once armed, close if R drops to (bestR - trailGivebackR).
    trailGivebackR: float


def load_config() -> ScalpManageConfig:
    cfg = get_settings()
    return ScalpManageConfig(
        minStopRatio=cfg.scalp_min_stop_ratio,
        emergencyR=cfg.scalp_emergency_r,
        watchR=cfg.scalp_watch_r,
        trailStartR=cfg.scalp_trail_start_r,
        trailGivebackR=cfg.scalp_trail_giveback_r,
    )


@dataclass(slots=True)
class TicketState:
    checks: int
    lastR: float
    bestR: float


@dataclass(slots=True)
class ScalpDecisionInput:
    #: Prior state for this ticket, or None on first sight.
    state: TicketState | None
    #: Current unrealized profit ÷ risk amount.
    r: float
    #: |signal.entry − signal.stop| — the stop distance we intended.
    intendedStopDist: float
    #: |position.openPrice − position.stopLoss| — the distance after the real fill.
    actualStopDist: float


@dataclass(slots=True)
class ScalpDecision:
    action: Literal["close", "hold"]
    reason: str
    nextState: TicketState


def decide_scalp_action(data: ScalpDecisionInput, cfg: ScalpManageConfig) -> ScalpDecision:
    """Whether to close (and why) plus the updated state.

    Precedence: unsafe-stop (first sight only) → single-check emergency →
    two-check adverse → profit give-back lock → hold.
    """
    state = data.state
    r = data.r
    first_check = state is None
    worsened = state is not None and r < state.lastR
    best_r = r if state is None else max(state.bestR, r)
    next_state = TicketState(
        checks=(state.checks if state else 0) + 1, lastR=r, bestR=best_r
    )

    # 1. Slippage-jammed stop — only meaningful on first sight (we never modify the SL).
    if (
        first_check
        and data.intendedStopDist > 0
        and data.actualStopDist / data.intendedStopDist < cfg.minStopRatio
    ):
        return ScalpDecision("close", "unsafe_stop_slippage", next_state)

    # 2. Single-check emergency.
    if r <= -cfg.emergencyR:
        return ScalpDecision("close", "emergency_adverse", next_state)

    # 3. Two-check adverse: worse than last check while already in adverse territory.
    if worsened and r <= -cfg.watchR:
        return ScalpDecision("close", "two_check_adverse", next_state)

    # 4. Profit give-back lock.
    if best_r >= cfg.trailStartR and r <= best_r - cfg.trailGivebackR:
        return ScalpDecision("close", "profit_lock", next_state)

    return ScalpDecision("hold", "hold", next_state)
