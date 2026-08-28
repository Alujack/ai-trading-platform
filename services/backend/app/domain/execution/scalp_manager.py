"""Fast active position management for scalps — port of `execution/scalpManager.ts`.

The 5-min reconciler (`monitor_live_trades`) only *records* closes the broker
already made (SL/TP/manual). Scalps need faster, active exits BEFORE the SL:

* unsafe-stop : on first sight, if the actual fill ate more than half the
                intended stop room (slippage jammed the fill toward the SL), close.
* emergency   : unrealized R <= -emergencyR in one check → close now.
* two-check   : this check worse than the last AND R <= -watchR → close.
* profit-lock : once R reaches trailStartR, close if it gives back trailGivebackR.

Manages only scalp-strategy trades (`SCALP_MANAGED_PREFIX`, default "scalp");
trend/swing trades are left to their structural SL/TP.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.serialization import num_or_nan
from ...core.settings import get_settings
from ...db.enums import TradeStatus
from ...db.models import Signal, Trade
from ...jobs.clock import utcnow
from .broker import get_broker
from .live_trade import finalize_live_close
from .scalp_decision import ScalpDecisionInput, TicketState, decide_scalp_action, load_config

log = logging.getLogger("backend.scalp")

# Per-ticket state across ticks. Cleared for tickets no longer open each tick.
_ticket_states: dict[str, TicketState] = {}


@dataclass(slots=True)
class ScalpManageSummary:
    managed: int
    closed: int
    held: int
    gone: int


async def run_scalp_management_tick(session: AsyncSession) -> ScalpManageSummary:
    """One management pass over open scalp trades.

    Polls the broker once, decides per position via `decide_scalp_action`, and
    closes (at market) + journals via `finalize_live_close` for any that trip a
    rule. Idempotent and safe to run every ~15s.
    """
    cfg = load_config()
    prefix = get_settings().scalp_managed_prefix.strip()
    trades = (
        (
            await session.execute(
                select(Trade)
                .join(Signal, Trade.signalId == Signal.id)
                .where(
                    Trade.status == TradeStatus.OPEN,
                    Trade.externalOrderId.is_not(None),
                    Signal.strategyName.startswith(prefix),
                )
            )
        )
        .scalars()
        .all()
    )

    if not trades:
        _ticket_states.clear()
        return ScalpManageSummary(managed=0, closed=0, held=0, gone=0)

    broker = get_broker()
    try:
        positions = await broker.get_positions()
    except Exception as exc:
        log.error("[scalpManager] get_positions failed: %s", exc)
        return ScalpManageSummary(
            managed=len(trades), closed=0, held=len(trades), gone=0
        )

    by_ticket = {str(p.ticket): p for p in positions}
    live_tickets: set[str] = set()
    closed = 0
    held = 0
    gone = 0

    for trade in trades:
        ticket = trade.externalOrderId or ""
        pos = by_ticket.get(ticket)
        if pos is None:
            # Ticket already gone from the broker — the 5-min reconciler records it.
            gone += 1
            continue
        live_tickets.add(ticket)

        risk_amount = num_or_nan(trade.riskAmount)
        if not risk_amount > 0:
            held += 1
            continue
        r = pos.profit / risk_amount
        intended_stop_dist = abs(
            num_or_nan(trade.signal.entryPrice) - num_or_nan(trade.signal.stopLoss)
        )
        actual_stop_dist = abs(pos.openPrice - pos.stopLoss)

        decision = decide_scalp_action(
            ScalpDecisionInput(
                state=_ticket_states.get(ticket),
                r=r,
                intendedStopDist=intended_stop_dist,
                actualStopDist=actual_stop_dist,
            ),
            cfg,
        )
        _ticket_states[ticket] = decision.nextState

        if decision.action != "close":
            held += 1
            continue

        try:
            result = await broker.close_position(ticket)
        except Exception as exc:
            log.error("[scalpManager] close failed ticket=%s: %s", ticket, exc)
            held += 1
            continue

        if result.status != "closed":
            # Most often "not_found" — the broker SL/TP already took it.
            log.warning(
                "[scalpManager] close ticket=%s -> %s (%s)",
                ticket,
                result.status,
                result.reason or "—",
            )
            held += 1
            continue

        exit_price = result.exitPrice if result.exitPrice is not None else pos.openPrice
        realized_profit = result.profit if result.profit is not None else pos.profit
        outcome = await finalize_live_close(
            session, trade, exit_price, realized_profit, decision.reason
        )
        _ticket_states.pop(ticket, None)
        closed += 1
        log.info(
            "[scalpManager] %s closed trade=%s %s ticket=%s reason=%s R=%.2f pnl=$%.2f %s",
            utcnow().isoformat(),
            trade.id,
            trade.signal.symbol,
            ticket,
            decision.reason,
            r,
            realized_profit,
            outcome["outcome"],
        )

    # Drop state for tickets no longer open (closed by us, the reconciler, or the broker).
    for ticket in list(_ticket_states):
        if ticket not in live_tickets:
            _ticket_states.pop(ticket, None)

    return ScalpManageSummary(managed=len(trades), closed=closed, held=held, gone=gone)
