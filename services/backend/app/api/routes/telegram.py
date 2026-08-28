"""Telegram settings, tests, webhook registration and callbacks.

Port of `routes/telegram.routes.ts`. The inbound webhook verifies the
secret-token header, authorizes the sender against the allowlist, then routes
Approve/Reject callbacks and text commands. It always responds 200 fast and does
the work in the background, exactly like the Express handler — Telegram retries
anything slower.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import func, select

from ...core.logging import get_logger
from ...core.serialization import num
from ...core.settings import get_settings
from ...db.enums import ApprovalStatus, ExecutionMode, TradeStatus
from ...db.models import Approval, Trade
from ...db.session import session_scope
from ...domain.config.defaults import SYMBOL_CURRENCIES
from ...domain.config.resolve import get_execution_map
from ...domain.config.store import arm_system, set_kill_switch, write_execution_mode
from ...domain.execution.policy import is_breaker_tripped_today
from ...domain.execution.review_agent import apply_recommendation_decision
from ...integrations.telegram.approvals import apply_approval_decision
from ...integrations.telegram.client import (
    allowed_user_ids,
    answer_callback_query,
    edit_message_text,
    get_webhook_info,
    register_webhook,
    send_message,
    webhook_secret,
)
from ...integrations.telegram.config import (
    clear_overrides,
    get_chat_id,
    is_configured,
    set_overrides,
)
from ...integrations.telegram.config import (
    status as telegram_status,
)
from ...jobs.clock import as_aware_utc, iso_stamp, start_of_utc_day, utcnow
from ..dependencies import Db

log = get_logger("backend.telegram")
router = APIRouter(tags=["telegram"])

KNOWN_SYMBOLS = frozenset(SYMBOL_CURRENCIES)


def _authorized(user_id: str | None) -> bool:
    allow = allowed_user_ids()
    if not allow:
        log.warning("[telegram] TELEGRAM_ALLOWED_USER_IDS empty — allowing all (dev mode)")
        return True
    return user_id is not None and user_id in allow


# ---- command handlers ------------------------------------------------------


async def _cmd_status(session: Db) -> str:
    base = get_settings().paper_account_balance
    realized = (
        await session.execute(
            select(func.sum(Trade.profitLoss)).where(Trade.status == TradeStatus.CLOSED)
        )
    ).scalar()
    open_count = (
        await session.execute(
            select(func.count()).select_from(Trade).where(Trade.status == TradeStatus.OPEN)
        )
    ).scalar() or 0
    exec_map = await get_execution_map(session)
    breaker = await is_breaker_tripped_today(session)
    today = (
        await session.execute(
            select(func.sum(Trade.profitLoss)).where(
                Trade.status == TradeStatus.CLOSED, Trade.closedAt >= start_of_utc_day()
            )
        )
    ).scalar()
    equity = base + num(realized)
    return "\n".join(
        [
            "📊 <b>STATUS</b>",
            f"Equity     ${equity:.2f}",
            f"Day P&L    ${num(today):.2f}",
            f"Open       {open_count}",
            f"Mode       {exec_map['global']} (global)",
            f"Breaker    {'🔴 ' + str(breaker.reason) if breaker.tripped else '🟢 clear'}",
        ]
    )


async def _cmd_positions(session: Db) -> str:
    open_trades = (
        (
            await session.execute(
                select(Trade)
                .where(Trade.status == TradeStatus.OPEN)
                .order_by(Trade.openedAt.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    if not open_trades:
        return "No open positions."
    lines = [
        f"• {t.signal.symbol} {t.signal.direction.value} "
        f"{num(t.positionSize):.4f} @ {_plain(num(t.entryPrice))} "
        f"(risk ${num(t.riskAmount):.0f})"
        for t in open_trades
    ]
    return "\n".join([f"📈 <b>OPEN POSITIONS</b> ({len(open_trades)})", *lines])


def _plain(value: float) -> str:
    """Render a price the way JS `${number}` interpolation does (no trailing .0)."""
    return str(int(value)) if float(value).is_integer() else str(value)


async def _cmd_pending(session: Db) -> str:
    pending = (
        (
            await session.execute(
                select(Approval)
                .where(Approval.status == ApprovalStatus.PENDING)
                .order_by(Approval.createdAt.asc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    if not pending:
        return "No approvals awaiting a decision."
    lines = []
    for a in pending:
        remaining = (as_aware_utc(a.expiresAt) - utcnow()).total_seconds() / 60
        mins = max(0, round(remaining))
        lines.append(
            f"• {a.signal.symbol} {a.signal.direction.value} "
            f"{a.signal.strategyName or ''} — expires {mins}m"
        )
    return "\n".join([f"⏳ <b>AWAITING APPROVAL</b> ({len(pending)})", *lines])


async def _cmd_mode(session: Db, actor: str, args: list[str]) -> str:
    mode_arg = (args[0] if args else "").upper()
    if mode_arg not in ("AUTO", "CONFIRM", "OFF"):
        return "Usage: /mode auto|confirm|off [SYMBOL|strategy]"
    mode = ExecutionMode(mode_arg)
    target = args[1] if len(args) > 1 else None
    if not target:
        await write_execution_mode(session, actor, "GLOBAL", "", mode)
        return f"Global mode → <b>{mode.value}</b>"
    if target.upper() in KNOWN_SYMBOLS:
        await write_execution_mode(session, actor, "SYMBOL", target.upper(), mode)
        return f"{target.upper()} mode → <b>{mode.value}</b>"
    await write_execution_mode(session, actor, "STRATEGY", target, mode)
    return f"Strategy {target} mode → <b>{mode.value}</b>"


async def handle_command(session: Db, actor: str, text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return "Unknown command. Send /help."
    cmd, args = parts[0].lower(), parts[1:]
    if cmd == "/status":
        return await _cmd_status(session)
    if cmd == "/positions":
        return await _cmd_positions(session)
    if cmd == "/pending":
        return await _cmd_pending(session)
    if cmd == "/mode":
        return await _cmd_mode(session, actor, args)
    if cmd == "/kill":
        await set_kill_switch(session, actor)
        return "🛑 <b>KILL</b> — global mode OFF. No new trades will open."
    if cmd == "/arm":
        await arm_system(session, actor)
        return "✅ <b>ARMED</b> — global mode CONFIRM."
    if cmd in ("/start", "/help"):
        return "\n".join(
            [
                "Commands:",
                "/status — equity, open, mode, breaker",
                "/positions — open trades",
                "/pending — approvals awaiting decision",
                "/mode auto|confirm|off [SYMBOL|strategy]",
                "/kill — global OFF (panic)",
                "/arm — clear OFF",
            ]
        )
    return "Unknown command. Send /help."


# ---- dashboard config surface ---------------------------------------------
# Lets the operator paste the bot token / chat id / allowlist from the UI instead
# of editing .env. Secrets are stored in a gitignored file and never returned —
# only presence flags + a token hint.


class SaveTelegramBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    botToken: str | None = Field(default=None, max_length=200)
    chatId: str | None = Field(default=None, max_length=64)
    # Telegram only accepts A-Z a-z 0-9 _ - in the webhook secret token. Reject
    # anything else up front so a bad value can't silently 401 every callback.
    # The empty-string case is allowed so the field can be cleared.
    webhookSecret: str | None = Field(default=None, max_length=256, pattern=r"^[A-Za-z0-9_-]*$")
    allowedUserIds: str | None = Field(default=None, max_length=200)


class RegisterWebhookBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publicUrl: HttpUrl = Field(max_length=300)


@router.get("/api/telegram")
async def get_telegram() -> dict[str, Any]:
    base = telegram_status()
    webhook = await get_webhook_info() if base["hasToken"] else None
    return {**base, "webhook": webhook}


@router.put("/api/telegram")
async def put_telegram(body: SaveTelegramBody) -> dict[str, Any]:
    set_overrides(body.model_dump(exclude_unset=True))
    return {"ok": True, **telegram_status()}


@router.delete("/api/telegram")
async def delete_telegram() -> dict[str, Any]:
    clear_overrides()
    return {"ok": True, **telegram_status()}


@router.post("/api/telegram/test")
async def test_telegram(response: Response) -> dict[str, Any]:
    """Send a test message to the configured chat — proves token + chat id work."""
    if not is_configured():
        response.status_code = 400
        return {"ok": False, "error": "Set a bot token and chat id first."}
    message_id = await send_message(
        get_chat_id(), "✅ AI Trading bot connected — test message."
    )
    if not message_id:
        response.status_code = 502
        return {
            "ok": False,
            "error": "Telegram rejected the send — check the token and chat id.",
        }
    return {"ok": True, "detail": "Test message sent."}


@router.post("/api/telegram/webhook")
async def post_register_webhook(
    body: RegisterWebhookBody, response: Response
) -> dict[str, Any]:
    """Register the inbound webhook at a public URL (e.g. a cloudflared tunnel)."""
    result = await register_webhook(str(body.publicUrl))
    if not result["ok"]:
        response.status_code = 502
        return {"ok": False, "error": result.get("error"), "url": result.get("url")}
    return {"ok": True, "url": result["url"]}


# ---- webhook ---------------------------------------------------------------


async def _handle_update(update: dict[str, Any]) -> None:
    """Do the webhook work off the response path, in its own session."""
    try:
        callback = update.get("callback_query")
        if callback:
            user_id = _str_id(callback.get("from", {}).get("id"))
            message = callback.get("message") or {}
            chat_id = _str_id((message.get("chat") or {}).get("id"))
            message_id = _str_id(message.get("message_id"))

            if not _authorized(user_id):
                if callback.get("id"):
                    await answer_callback_query(callback["id"], "Not authorized.")
                return

            data = str(callback.get("data") or "")
            kind, _, target_id = data.partition(":")
            decided_by = f"telegram:{user_id}"

            async with session_scope() as session:
                # Agent-recommendation cards (weekly review config proposals).
                if kind in ("rca", "rcr") and target_id:
                    result = await apply_recommendation_decision(
                        session, target_id, kind == "rca", decided_by
                    )
                elif kind in ("apv", "rej") and target_id:
                    result = await apply_approval_decision(
                        session, target_id, kind == "apv", decided_by
                    )
                else:
                    if callback.get("id"):
                        await answer_callback_query(callback["id"], "Unrecognized action.")
                    return

            if callback.get("id"):
                await answer_callback_query(callback["id"], result["message"])
            if chat_id and message_id:
                await edit_message_text(
                    chat_id, message_id, f"{result['message']} · {iso_stamp()} UTC"
                )
            return

        message = update.get("message") or {}
        if message.get("text"):
            user_id = _str_id((message.get("from") or {}).get("id"))
            chat_id = _str_id((message.get("chat") or {}).get("id"))
            if not chat_id:
                return
            if not _authorized(user_id):
                await send_message(chat_id, "Not authorized.")
                return
            async with session_scope() as session:
                reply = await handle_command(
                    session, f"telegram:{user_id}", str(message.get("text") or "")
                )
            await send_message(chat_id, reply)
    except Exception as exc:
        log.error("[telegram] webhook handler error: %s", exc)


def _str_id(value: Any) -> str | None:
    return None if value is None else str(value)


@router.post("/api/internal/telegram/webhook")
async def telegram_webhook(
    request: Request,
    response: Response,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Inbound Telegram webhook. Verifies the secret token, then acks immediately."""
    secret = webhook_secret()
    if secret and x_telegram_bot_api_secret_token != secret:
        response.status_code = 401
        return {"error": "bad secret"}

    try:
        update = await request.json()
    except Exception:
        update = {}
    if not isinstance(update, dict):
        update = {}

    # Acknowledge immediately; do the work without blocking the response.
    asyncio.get_running_loop().create_task(_handle_update(update))
    return {"ok": True}
