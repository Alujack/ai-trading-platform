"""Thin Telegram Bot API client — port of `telegram/telegram.ts`.

All network calls are best-effort and never raise into the caller: a Telegram
outage must fail closed (the signal stays PENDING), never crash the execution
path.
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

import httpx

from .config import (
    get_allowed_user_ids,
    get_bot_token,
    get_chat_id,
    get_webhook_secret,
    is_configured as cfg_configured,
)

log = logging.getLogger("backend.telegram")

API_BASE = "https://api.telegram.org"
WEBHOOK_PATH = "/api/internal/telegram/webhook"
_TIMEOUT_S = 15.0


class InlineButton(TypedDict):
    text: str
    callback_data: str


def default_chat_id() -> str | None:
    return get_chat_id() or None


def is_configured() -> bool:
    return cfg_configured()


def allowed_user_ids() -> list[str]:
    """Allowlisted Telegram user ids permitted to approve/command."""
    return get_allowed_user_ids()


def webhook_secret() -> str | None:
    return get_webhook_secret() or None


async def _call(method: str, payload: dict[str, Any]) -> Any | None:
    token = get_bot_token()
    if not token:
        log.warning("[telegram] %s skipped — TELEGRAM_BOT_TOKEN not set", method)
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            res = await client.post(f"{API_BASE}/bot{token}/{method}", json=payload)
            body = res.json()
    except Exception as exc:  # noqa: BLE001
        log.error("[telegram] %s error: %s", method, exc)
        return None
    if not body.get("ok"):
        log.error("[telegram] %s failed: %s", method, body.get("description") or res.status_code)
        return None
    return body.get("result")


async def send_message(
    chat_id: str, text: str, buttons: list[list[InlineButton]] | None = None
) -> str | None:
    """Send a message with an optional inline keyboard. Returns the message_id."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = await _call("sendMessage", payload)
    return str(result["message_id"]) if result else None


async def edit_message_text(chat_id: str, message_id: str, text: str) -> None:
    """Edit a message in place and drop its buttons (used to stamp an outcome)."""
    await _call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": []},
        },
    )


async def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    """Clear the user's loading spinner after a button tap."""
    await _call(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text or ""},
    )


async def register_webhook(public_url: str) -> dict[str, Any]:
    """Register the inbound webhook at `<publicUrl>/api/internal/telegram/webhook`."""
    token = get_bot_token()
    url = f"{public_url.rstrip('/')}{WEBHOOK_PATH}"
    if not token:
        return {"ok": False, "url": url, "error": "bot token not set"}
    payload: dict[str, Any] = {
        "url": url,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }
    secret = get_webhook_secret()
    if secret:
        payload["secret_token"] = secret
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            res = await client.post(f"{API_BASE}/bot{token}/setWebhook", json=payload)
            body = res.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": str(exc)}
    if not body.get("ok"):
        return {"ok": False, "url": url, "error": body.get("description") or f"http {res.status_code}"}
    return {"ok": True, "url": url}


async def get_webhook_info() -> dict[str, Any] | None:
    result = await _call("getWebhookInfo", {})
    if not result:
        return None
    return {
        "url": result.get("url"),
        "pending": result.get("pending_update_count"),
        "lastError": result.get("last_error_message"),
    }


def esc(text: str) -> str:
    """Escape the characters Telegram's HTML parse_mode treats specially."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
