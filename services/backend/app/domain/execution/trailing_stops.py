"""Trailing Stop Manager — port of `execution/trailingStopManager.ts`.

ATR-based dynamic trade management, pure and side-effect free:

1. **Breakeven Lock** — move SL to entry + spread after 0.5×ATR profit.
2. **ATR Trailing Stop** — after 1×ATR profit, trail the stop by a configurable
   ATR multiple (default 1.5×ATR behind price).
3. **Time-based Exit** — close any trade open > maxHoldMinutes to avoid holding
   through low-volume dead zones.
4. **Partial Profit Taking** — scale out 50% at TP1 (1×ATR), trail the rest.

Like the TypeScript original this is a decision core with no caller wired in yet:
the scalp manager owns the live 15s loop today, and this stays available for the
ATR-managed swing path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from ...jobs.clock import as_aware_utc, utcnow

log = logging.getLogger("backend.trailing")


@dataclass(frozen=True, slots=True)
class TrailingStopConfig:
    #: Move SL to breakeven after this × ATR profit.
    breakevenTriggerAtr: float = 0.5
    #: Spread to add beyond entry for breakeven (price units; 0.20 suits Gold).
    breakevenSpread: float = 0.20
    #: Start trailing after this × ATR profit.
    trailTriggerAtr: float = 1.0
    #: Trail distance: keep SL this × ATR behind price.
    trailDistanceAtr: float = 1.5
    #: Max time to hold a trade, in minutes.
    maxHoldMinutes: float = 240
    #: Partial take-profit at this × ATR.
    partialTpAtr: float = 1.0
    #: Fraction of the position to close at partial TP.
    partialTpFraction: float = 0.5


DEFAULT_TRAILING_CONFIG = TrailingStopConfig()


@dataclass(slots=True)
class TrailingStopInput:
    entryPrice: float
    currentPrice: float
    currentStop: float
    direction: str
    atr: float
    openedAt: datetime
    now: datetime | None = None
    config: TrailingStopConfig | None = None
    #: Whether partial TP has already been taken.
    partialTaken: bool = False
    #: Current position size (lots).
    positionSize: float | None = None


@dataclass(slots=True)
class TrailingStopAction:
    type: Literal["HOLD", "TRAIL", "BREAKEVEN", "PARTIAL_CLOSE", "TIME_EXIT"]
    reason: str
    newStop: float | None = None
    closeSize: float | None = None


def evaluate_trailing_stop(data: TrailingStopInput) -> TrailingStopAction:
    """Decide the next management action for one open position."""
    cfg = data.config or DEFAULT_TRAILING_CONFIG
    now = as_aware_utc(data.now) if data.now else utcnow()
    opened_at = as_aware_utc(data.openedAt)

    # 1. Time-based exit check.
    hold_minutes = (now - opened_at).total_seconds() / 60
    if hold_minutes >= cfg.maxHoldMinutes:
        log.info(
            "[trailing] time_exit holdMinutes=%.1f maxHoldMinutes=%s",
            hold_minutes,
            cfg.maxHoldMinutes,
        )
        return TrailingStopAction(
            type="TIME_EXIT",
            reason=(
                f"Trade open {round(hold_minutes)}min exceeds "
                f"{_fmt(cfg.maxHoldMinutes)}min limit"
            ),
        )

    # Unrealized profit in ATR units.
    unrealized = (
        data.currentPrice - data.entryPrice
        if data.direction == "LONG"
        else data.entryPrice - data.currentPrice
    )
    profit_atr = unrealized / data.atr if data.atr > 0 else 0.0

    # 2. Partial profit taking (if not already taken and position size known).
    if (
        not data.partialTaken
        and data.positionSize
        and data.positionSize > 0
        and profit_atr >= cfg.partialTpAtr
    ):
        close_size = round(data.positionSize * cfg.partialTpFraction, 2)
        be_stop = (
            data.entryPrice + cfg.breakevenSpread
            if data.direction == "LONG"
            else data.entryPrice - cfg.breakevenSpread
        )
        log.info(
            "[trailing] partial_close profitAtr=%.2f closeSize=%s newStop=%s",
            profit_atr,
            close_size,
            be_stop,
        )
        return TrailingStopAction(
            type="PARTIAL_CLOSE",
            closeSize=close_size,
            newStop=be_stop,
            reason=(
                f"Partial TP: {profit_atr:.1f}×ATR profit, closing "
                f"{cfg.partialTpFraction * 100:.0f}% ({close_size} lots), SL → breakeven"
            ),
        )

    # 3. ATR trailing stop (higher priority than breakeven if we're far enough).
    if profit_atr >= cfg.trailTriggerAtr:
        trail_stop = (
            data.currentPrice - cfg.trailDistanceAtr * data.atr
            if data.direction == "LONG"
            else data.currentPrice + cfg.trailDistanceAtr * data.atr
        )
        # Only move the stop if the new level is BETTER (closer to current price)
        # than the existing stop. Never widen a stop.
        is_better = (
            trail_stop > data.currentStop
            if data.direction == "LONG"
            else trail_stop < data.currentStop
        )
        if is_better:
            log.info(
                "[trailing] trail profitAtr=%.2f currentStop=%s newStop=%s",
                profit_atr,
                data.currentStop,
                trail_stop,
            )
            return TrailingStopAction(
                type="TRAIL",
                newStop=round(trail_stop, 2),
                reason=(
                    f"Trailing: {profit_atr:.1f}×ATR profit, SL → {trail_stop:.2f} "
                    f"({_fmt(cfg.trailDistanceAtr)}×ATR behind price)"
                ),
            )

    # 4. Breakeven lock.
    if profit_atr >= cfg.breakevenTriggerAtr:
        be_stop = (
            data.entryPrice + cfg.breakevenSpread
            if data.direction == "LONG"
            else data.entryPrice - cfg.breakevenSpread
        )
        # Only move to breakeven if the current stop is still beyond entry.
        not_yet_be = (
            data.currentStop < data.entryPrice
            if data.direction == "LONG"
            else data.currentStop > data.entryPrice
        )
        if not_yet_be:
            log.info(
                "[trailing] breakeven profitAtr=%.2f currentStop=%s newStop=%s",
                profit_atr,
                data.currentStop,
                be_stop,
            )
            return TrailingStopAction(
                type="BREAKEVEN",
                newStop=round(be_stop, 2),
                reason=(
                    f"Breakeven lock: {profit_atr:.1f}×ATR profit, "
                    f"SL → {be_stop:.2f} (entry + spread)"
                ),
            )

    # 5. No action needed.
    return TrailingStopAction(
        type="HOLD", reason=f"Profit {profit_atr:.1f}×ATR — below trigger thresholds"
    )


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def with_overrides(**overrides) -> TrailingStopConfig:
    """`{ ...DEFAULT_TRAILING_CONFIG, ...partial }` for callers passing a subset."""
    return replace(DEFAULT_TRAILING_CONFIG, **overrides)
