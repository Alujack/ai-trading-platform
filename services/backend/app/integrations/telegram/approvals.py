"""Human-in-the-loop approvals — port of `apps/api/src/telegram/approvals.ts`.

A CONFIRM-mode signal gets an `Approval(PENDING)` row plus a Telegram card with
Approve/Reject buttons. Fail-safe throughout: if Telegram isn't configured or
the send fails, the Approval is still recorded so the signal is auditable and the
expiry sweep can clean it up — the signal never auto-opens as a fallback.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...core.realtime import publish_event
from ...core.serialization import num
from ...core.settings import get_settings
from ...db.enums import ApprovalStatus, SignalStatus
from ...db.models import Approval, Signal
from ...domain.config.resolve import resolve_risk_config
from ...domain.risk.engine import calculate_position_size
from ...jobs.clock import as_aware_utc, naive_utcnow, utcnow
from .client import (
    default_chat_id,
    edit_message_text,
    esc,
    is_configured,
    send_message,
)

log = logging.getLogger("backend.approvals")


def _fmt(symbol: str, value: float) -> str:
    """Price formatting per instrument, mirroring the TS `toLocaleString` call."""
    dp = 4 if symbol == "EURUSD" else 0 if symbol == "BTCUSD" else 2
    return f"{value:,.{dp}f}"


async def format_alert(session: AsyncSession, signal: Signal, ttl_min: int) -> str:
    """Build the full alert text (plan + AI reasoning) from data the gate produced."""
    cfg = await resolve_risk_config(session, signal.strategyName, signal.symbol)
    balance = get_settings().paper_account_balance
    entry = num(signal.entryPrice)
    stop = num(signal.stopLoss)
    target = num(signal.takeProfit)
    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = reward / risk if risk > 0 else 0.0

    size_line = "—"
    try:
        sized = calculate_position_size(balance, cfg.riskPerTradePct, entry, stop)
        pct = cfg.riskPerTradePct
        pct_text = str(int(pct)) if float(pct).is_integer() else str(pct)
        size_line = (
            f"{sized.lotSize:.4f} units  (risk ${sized.riskAmount:.2f} = "
            f"{pct_text}% of ${balance:,.0f})"
        )
    except ValueError:
        pass  # leave the dash

    dot = "🟢" if signal.direction.value == "LONG" else "🔴"
    reasoning = (signal.aiReasoning or "").strip()[:600]

    return "\n".join(
        [
            f"{dot} <b>SIGNAL</b> — {esc(signal.symbol)} {esc(signal.timeframe)} · "
            f"{signal.direction.value} · {esc(signal.strategyName or '—')}",
            f"Mode: CONFIRM · expires in {ttl_min}m",
            "",
            "<b>PLAN</b>",
            f"• Entry   {_fmt(signal.symbol, entry)}",
            f"• Stop    {_fmt(signal.symbol, stop)}",
            f"• Target  {_fmt(signal.symbol, target)}",
            f"• R:R     1:{rr:.2f}",
            f"• Size    {size_line}",
            "",
            f"<b>WHY</b> (AI score {signal.confidenceScore}/100)",
            esc(reasoning) or "—",
        ]
    )


async def request_approval(
    session: AsyncSession, signal: Signal, ttl_min: int
) -> dict[str, Any]:
    """Create an `Approval(PENDING)` and send the Telegram alert with buttons."""
    if get_settings().api_shadow_mode:
        return {"created": False, "reason": "shadow_mode_no_telegram"}

    existing = (
        await session.execute(select(Approval).where(Approval.signalId == signal.id))
    ).scalar_one_or_none()
    if existing is not None:
        return {"created": False, "reason": "approval_exists"}

    chat_id = default_chat_id() or ""
    approval = Approval(
        id=new_id(),
        signalId=signal.id,
        status=ApprovalStatus.PENDING,
        chatId=chat_id,
        expiresAt=naive_utcnow() + timedelta(minutes=ttl_min),
        createdAt=naive_utcnow(),
    )
    session.add(approval)
    await session.commit()

    if not is_configured():
        log.warning(
            "[approvals] Telegram not configured — approval %s created without alert",
            approval.id,
        )
        return {"created": True, "reason": "telegram_not_configured"}

    text = await format_alert(session, signal, ttl_min)
    message_id = await send_message(
        chat_id,
        text,
        [
            [
                {"text": "✅ Approve", "callback_data": f"apv:{approval.id}"},
                {"text": "❌ Reject", "callback_data": f"rej:{approval.id}"},
            ]
        ],
    )
    if message_id:
        approval.messageId = message_id
        await session.commit()
    return {"created": True}


async def apply_approval_decision(
    session: AsyncSession, approval_id: str, approve: bool, decided_by: str
) -> dict[str, Any]:
    """Apply an Approve/Reject decision from the Telegram webhook.

    Idempotent: a second tap on an already-decided/expired approval is a no-op.
    """
    from ...domain.execution.live_trade import open_live_trade
    from ...domain.execution.paper_trading import open_paper_trade

    approval = (
        await session.execute(select(Approval).where(Approval.id == approval_id))
    ).scalar_one_or_none()
    if approval is None:
        return {"ok": False, "outcome": "not_found", "message": "Approval not found."}

    if approval.status != ApprovalStatus.PENDING:
        return {
            "ok": False,
            "outcome": "already_decided",
            "message": f"Already {approval.status.value.lower()}.",
        }
    if as_aware_utc(approval.expiresAt) < utcnow():
        approval.status = ApprovalStatus.EXPIRED
        approval.signal.status = SignalStatus.CANCELLED
        await session.commit()
        return {"ok": False, "outcome": "expired", "message": "This signal already expired."}

    stamp = naive_utcnow()
    if not approve:
        approval.status = ApprovalStatus.REJECTED
        approval.decidedBy = decided_by
        approval.decidedAt = stamp
        approval.signal.status = SignalStatus.CANCELLED
        await session.commit()
        await publish_event("signal", symbol=approval.signal.symbol)
        return {"ok": True, "outcome": "rejected", "message": f"❌ Rejected by {decided_by}"}

    # Approve → the authoritative risk re-size happens inside the trade opener.
    opener = open_live_trade if get_settings().is_live_broker else open_paper_trade
    opened = await opener(session, approval.signalId)
    if opened.status != "opened":
        return {
            "ok": False,
            "outcome": "open_failed",
            "message": f"Could not open: {opened.reason or 'unknown'}",
        }
    approval.status = ApprovalStatus.APPROVED
    approval.decidedBy = decided_by
    approval.decidedAt = stamp
    await session.commit()
    await publish_event("trade", symbol=approval.signal.symbol)
    return {
        "ok": True,
        "outcome": "approved",
        "message": f"✅ Approved by {decided_by} · trade opened",
    }


async def expire_stale_approvals(session: AsyncSession) -> dict[str, int]:
    """Expire any PENDING approval past its TTL: mark EXPIRED, cancel the signal,
    and stamp the Telegram message. Run every minute from the scheduler."""
    stale = (
        (
            await session.execute(
                select(Approval)
                .where(
                    Approval.status == ApprovalStatus.PENDING,
                    Approval.expiresAt < naive_utcnow(),
                )
                .limit(100)
            )
        )
        .scalars()
        .all()
    )

    expired = 0
    for approval in stale:
        approval.status = ApprovalStatus.EXPIRED
        approval.signal.status = SignalStatus.CANCELLED
        symbol = approval.signal.symbol
        await session.commit()
        if approval.messageId:
            await edit_message_text(
                approval.chatId, approval.messageId, "⌛ <b>Expired</b> — not taken."
            )
        await publish_event("signal", symbol=symbol)
        expired += 1
    return {"expired": expired}
