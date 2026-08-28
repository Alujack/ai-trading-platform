"""Telegram credentials, resolved UI-override ► .env — port of `telegram/config.ts`.

A value pasted in the dashboard is persisted to a gitignored JSON file and takes
precedence over the environment, so the operator never has to edit `.env` or
restart to connect a bot. Secrets are never returned to the client — only
presence flags and a token hint.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal, TypedDict

from ...core.settings import get_settings

log = logging.getLogger("backend.telegram")


class TelegramOverrides(TypedDict, total=False):
    botToken: str
    chatId: str
    webhookSecret: str
    allowedUserIds: str  # comma-separated


FIELDS: tuple[str, ...] = ("botToken", "chatId", "webhookSecret", "allowedUserIds")

_ENV_KEYS = {
    "botToken": "TELEGRAM_BOT_TOKEN",
    "chatId": "TELEGRAM_CHAT_ID",
    "webhookSecret": "TELEGRAM_WEBHOOK_SECRET",
    "allowedUserIds": "TELEGRAM_ALLOWED_USER_IDS",
}

_cache: TelegramOverrides | None = None


def overrides_path() -> Path:
    """Where the UI-set overrides live (env-relocatable for Docker/tests)."""
    configured = get_settings().telegram_overrides_path
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / ".telegram-overrides.json"


def _load() -> TelegramOverrides:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = json.loads(overrides_path().read_text("utf-8")) or {}
    except Exception:  # noqa: BLE001 — a missing/corrupt file just means "no overrides"
        _cache = {}
    return _cache


def _persist(nxt: TelegramOverrides) -> None:
    global _cache
    _cache = nxt
    try:
        path = overrides_path()
        path.write_text(json.dumps(nxt, indent=2), "utf-8")
        os.chmod(path, 0o600)
    except Exception as exc:  # noqa: BLE001
        log.error("[telegram] failed to persist overrides: %s", exc)


def _env(key: str) -> str:
    return os.environ.get(key) or ""


def get_bot_token() -> str:
    return _load().get("botToken") or _env("TELEGRAM_BOT_TOKEN")


def get_chat_id() -> str:
    return _load().get("chatId") or _env("TELEGRAM_CHAT_ID")


def get_webhook_secret() -> str:
    return _load().get("webhookSecret") or _env("TELEGRAM_WEBHOOK_SECRET")


def get_allowed_user_ids() -> list[str]:
    raw = _load().get("allowedUserIds") or _env("TELEGRAM_ALLOWED_USER_IDS")
    return [s.strip() for s in raw.split(",") if s.strip()]


def is_configured() -> bool:
    return bool(get_bot_token() and get_chat_id())


def set_overrides(partial: dict[str, str | None]) -> None:
    """Merge a partial set of overrides (empty string clears that field)."""
    nxt: TelegramOverrides = dict(_load())  # type: ignore[assignment]
    for key in FIELDS:
        if key not in partial:
            continue
        value = partial[key]
        if value is None:
            continue
        if value == "":
            nxt.pop(key, None)  # type: ignore[misc]
        else:
            nxt[key] = value  # type: ignore[literal-required]
    _persist(nxt)


def clear_overrides() -> None:
    _persist({})
    try:
        overrides_path().unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("[telegram] could not remove overrides file: %s", exc)


def _source(field: str) -> Literal["ui", "env", "none"]:
    if _load().get(field):  # type: ignore[arg-type]
        return "ui"
    if _env(_ENV_KEYS[field]):
        return "env"
    return "none"


def status() -> dict[str, object]:
    """Non-secret status for the dashboard. Never includes raw token/secret."""
    token = get_bot_token()
    return {
        "configured": is_configured(),
        "hasToken": bool(token),
        "tokenHint": f"…{token[-6:]}" if token else None,
        "chatId": get_chat_id() or None,
        "allowedUserIds": get_allowed_user_ids(),
        "hasWebhookSecret": bool(get_webhook_secret()),
        "sources": {field: _source(field) for field in FIELDS},
    }


def reset_cache() -> None:
    """Test hook: drop the memoized overrides so a fresh file is re-read."""
    global _cache
    _cache = None
