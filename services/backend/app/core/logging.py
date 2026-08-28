"""Structured, secret-redacting logging.

Every broker password, Telegram token and LLM key that could reach a log line
goes through `redact()` first — Phase 5's exit gate is "no secret appears in API
responses or logs".
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

_SECRET_KEYS = {
    "password",
    "passwordenc",
    "apikey",
    "api_key",
    "token",
    "bottoken",
    "bot_token",
    "webhooksecret",
    "webhook_secret",
    "secret",
    "encryption_key",
    "encryptionkey",
    "authorization",
    "x-bridge-token",
}

# Bot tokens (12345:AA...), long hex blobs, and bearer values in free text.
_INLINE_PATTERNS = (
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[0-9a-f]{32}:[0-9a-f]{32}:[0-9a-f]{8,}\b"),
)

REDACTED = "«redacted»"


def redact(value: Any) -> Any:
    """Recursively strip secrets from a value before it is logged."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[k] = REDACTED if str(k).lower() in _SECRET_KEYS else redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        text = value
        for pattern in _INLINE_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text
    return value


class RedactingFormatter(logging.Formatter):
    """Formatter that scrubs the rendered message as a last line of defence."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return str(redact(rendered))


def configure_logging(level: str = "info") -> None:
    """Install the redacting formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    # uvicorn installs its own handlers; make them inherit ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
