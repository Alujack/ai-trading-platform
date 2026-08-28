"""Live broker execution + position reconciliation — port of `execution/liveTrade.ts`.

* :func:`open_live_trade` — size via the broker tick-value formula, place via
  `get_broker().place_order()`, persist the `Trade` with
  externalOrderId / brokerFillPrice / broker.
* :func:`monitor_live_trades` — poll `get_broker().get_positions()`, detect
  closed tickets, fetch deal history, write `Trade.CLOSED` + a `Journal` entry.

Only reached when `BROKER=exness`; the paper path (`paper_trading.py`) is
unchanged and still runs when `BROKER=paper`.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...core.realtime import publish_event
from ...core.serialization import dec, iso, num_or_nan
from ...core.settings import get_settings
from ...db.enums import SignalStatus, TradeStatus
from ...db.models import Journal, Signal, Trade
from ...integrations.ai import client as ai
from ...integrations.ai.schemas import TradeReviewRequest
from ...jobs.clock import naive_utcnow
from ..config.resolve import resolve_risk_config
from .broker import PlaceOrderRequest, get_broker, lots_from_units
from .paper_trading import OpenResult, format_ai_review

log = logging.getLogger("backend.live")


async def open_live_trade(session: AsyncSession, signal_id: str) -> OpenResult:
    """Place a live order via the configured broker (ExnessBroker → MT5 bridge).

    Sizing: risk$ ÷ (stop-distance-in-ticks × tick-value-per-lot), then clamped
    to the broker's volumeMin / volumeStep / volumeMax. The broker's live account
    balance is used so position size tracks real equity.
    """
    if get_settings().api_shadow_mode:
        return OpenResult("skipped", "shadow_mode_no_execution")

    signal = (
        await session.execute(select(Signal).where(Signal.id == signal_id))
    ).scalar_one_or_none()
    if signal is None:
        return OpenResult("skipped", "signal_not_found")
    if signal.trades:
        return OpenResult("skipped", "already_has_trade")
    if signal.status != SignalStatus.PENDING:
        return OpenResult("skipped", f"signal_status_{signal.status.value}")

    entry = num_or_nan(signal.entryPrice)
    stop = num_or_nan(signal.stopLoss)
    tp = num_or_nan(signal.takeProfit)
    if not math.isfinite(entry) or not math.isfinite(stop) or entry == stop:
        return OpenResult("skipped", "invalid_levels")

    broker = get_broker()

    # Refuse to place if the bridge is unhealthy — existing positions have
    # server-side SL/TP and are safe, but we must not open new ones into a
    # degraded connection (MT5 outage fails closed).
    health = await broker.health()
    if not health.ok:
        return OpenResult("skipped", f"broker_unhealthy: {health.detail or 'unknown'}")

    try:
        account = await broker.get_account()
        spec = await broker.get_symbol_spec(signal.symbol)
    except Exception as exc:
        return OpenResult("skipped", f"broker_spec_error: {exc}")

    # Per-strategy risk % (STRATEGY-scope RiskConfig) so a stacking scalper can
    # size each add small while swing strategies keep their own risk.
    cfg = await resolve_risk_config(session, signal.strategyName, signal.symbol)
    risk_percent = cfg.riskPerTradePct or get_settings().paper_risk_percent
    risk_amount = account.balance * (risk_percent / 100)

    stop_ticks = abs(entry - stop) / spec.point if spec.point else 0.0
    raw_lots = (
        risk_amount / (stop_ticks * spec.tickValue)
        if stop_ticks > 0 and spec.tickValue > 0
        else 0.0
    )
    # lots_from_units expects "units" (raw_lots × contractSize) → step-clamped lots.
    lots = lots_from_units(raw_lots * spec.contractSize, spec)

    if lots <= 0:
        return OpenResult(
            "skipped",
            f"lots_below_minimum (rawLots={raw_lots:.6f} volumeMin={spec.volumeMin})",
        )

    try:
        result = await broker.place_order(
            PlaceOrderRequest(
                symbol=signal.symbol,
                side=signal.direction.value,
                lots=lots,
                stopLoss=stop,
                takeProfit=tp,
                clientTag=signal_id,
            )
        )
    except Exception as exc:
        return OpenResult("skipped", f"order_error: {exc}")

    if result.status != "filled":
        return OpenResult("skipped", f"order_rejected: {result.reason or 'unknown'}")

    fill_price = result.fillPrice if result.fillPrice is not None else entry

    trade = Trade(
        id=new_id(),
        signalId=signal.id,
        entryPrice=dec(fill_price, 8),
        positionSize=dec(lots, 8),
        riskAmount=dec(risk_amount, 2),
        status=TradeStatus.OPEN,
        openedAt=naive_utcnow(),
        externalOrderId=None if result.ticket is None else str(result.ticket),
        brokerFillPrice=dec(fill_price, 8),
        broker=broker.name,
    )
    signal.status = SignalStatus.ACTIVE
    session.add(trade)
    await session.commit()

    log.info(
        "[liveTrade] opened trade=%s signal=%s %s/%s lots=%s ticket=%s",
        trade.id,
        signal_id,
        signal.symbol,
        signal.direction.value,
        lots,
        result.ticket or "?",
    )
    return OpenResult("opened", tradeId=trade.id)


@dataclass(slots=True)
class MonitorSummary:
    inspected: int
    closed: int
    unchanged: int


async def _review_live_close(trade: Trade, exit_price: float, profit: float, r_multiple: float,
                             exit_reason: str, closed_at) -> Any | None:
    sig = trade.signal
    try:
        request = TradeReviewRequest.model_validate(
            {
                "trade": {
                    "symbol": sig.symbol,
                    "direction": sig.direction.value,
                    "strategyName": sig.strategyName,
                    "entryPrice": num_or_nan(trade.entryPrice),
                    "stopLoss": num_or_nan(sig.stopLoss),
                    "takeProfit": num_or_nan(sig.takeProfit),
                    "exitPrice": exit_price,
                    "profitLoss": profit,
                    "rMultiple": r_multiple,
                    "exitReason": exit_reason,
                    "openedAt": iso(trade.openedAt),
                    "closedAt": iso(closed_at),
                    "plannedReasoning": sig.aiReasoning,
                },
                "candles": [],
                "indicators": [],
            }
        )
        return await ai.trade_review(request)
    except Exception:
        return None


async def finalize_live_close(
    session: AsyncSession,
    trade: Trade,
    exit_price: float,
    realized_profit: float,
    exit_reason: str,
) -> dict[str, Any]:
    """Record a closed live trade: `Trade.CLOSED` + `Signal.CLOSED` + `Journal`.

    Shared by the 5-min reconciler (`exit_reason="broker_close"` — SL/TP/manual
    close) and the fast scalp manager (`"two_check_adverse"`, `"unsafe_stop"`, …),
    so every close is journaled identically regardless of who closed it.
    """
    sig = trade.signal
    entry = num_or_nan(trade.entryPrice)
    risk_amount = num_or_nan(trade.riskAmount)
    lots = num_or_nan(trade.positionSize)
    r_multiple = realized_profit / risk_amount if risk_amount > 0 else 0.0
    outcome = "WIN" if realized_profit > 0 else "LOSS" if realized_profit < 0 else "BREAKEVEN"
    closed_at = naive_utcnow()
    ticket = trade.externalOrderId or "?"

    review = await _review_live_close(
        trade, exit_price, realized_profit, r_multiple, exit_reason, closed_at
    )

    notes = (
        f"Live close ({exit_reason}). {sig.direction.value} {sig.symbol} {sig.timeframe}. "
        f"Outcome: {outcome}. Entry {entry:.5f} → Exit {exit_price:.5f}. "
        f"Lots {lots:.4f}, P&L ${realized_profit:.2f}, R {r_multiple:.2f}. "
        f"MT5 ticket #{ticket}."
    )
    ai_review = format_ai_review(review, "(per-trade AI review unavailable at live close)")

    trade.exitPrice = dec(exit_price, 8)
    trade.profitLoss = dec(realized_profit, 2)
    trade.status = TradeStatus.CLOSED
    trade.closedAt = closed_at
    sig.status = SignalStatus.CLOSED
    session.add(
        Journal(
            id=new_id(),
            tradeId=trade.id,
            notes=notes,
            aiReview=ai_review,
            grade=getattr(review, "grade", None),
            outcome=outcome,
            lesson=getattr(review, "lesson", None),
            rMultiple=dec(r_multiple, 4),
            createdAt=closed_at,
        )
    )
    await session.commit()

    await publish_event("trade", symbol=sig.symbol)
    return {"outcome": outcome, "rMultiple": r_multiple}


async def monitor_live_trades(session: AsyncSession) -> MonitorSummary:
    """Reconcile open trades (externalOrderId set) against live broker positions.

    If a ticket is gone from the broker's open-position list, the broker closed it
    (SL/TP hit or manual close in the terminal). We fetch deal history, write the
    `Trade.CLOSED` record, and create a `Journal` entry with an AI grade.
    """
    open_trades = (
        (
            await session.execute(
                select(Trade).where(
                    Trade.status == TradeStatus.OPEN, Trade.externalOrderId.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )

    if not open_trades:
        return MonitorSummary(inspected=0, closed=0, unchanged=0)

    broker = get_broker()
    try:
        live_positions = await broker.get_positions()
    except Exception as exc:
        log.error("[liveTrade] get_positions failed: %s", exc)
        return MonitorSummary(
            inspected=len(open_trades), closed=0, unchanged=len(open_trades)
        )

    open_tickets = {str(p.ticket) for p in live_positions}
    closed = 0
    unchanged = 0

    for trade in open_trades:
        ticket = trade.externalOrderId or ""
        if ticket in open_tickets:
            unchanged += 1
            continue

        # Ticket is gone — fetch deal history for the accurate close price and P&L.
        exit_price = num_or_nan(trade.entryPrice)  # safe fallback
        realized_profit = 0.0
        try:
            history = await broker.get_position_history(ticket)
            if history and history.found and history.exitPrice is not None:
                exit_price = history.exitPrice
                realized_profit = history.profit or 0.0
        except Exception as exc:
            log.warning("[liveTrade] history fetch failed ticket=%s: %s", ticket, exc)

        result = await finalize_live_close(
            session, trade, exit_price, realized_profit, "broker_close"
        )
        log.info(
            "[liveTrade] reconciled close trade=%s %s ticket=%s %s pnl=$%.2f R=%.2f",
            trade.id,
            trade.signal.symbol,
            ticket,
            result["outcome"],
            realized_profit,
            result["rMultiple"],
        )
        closed += 1

    return MonitorSummary(inspected=len(open_trades), closed=closed, unchanged=unchanged)
