"""Anthropic client wrapper with structured-output parsing and prompt caching."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, TypeVar

import anthropic
from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError

from .settings import Settings, get_settings

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=BaseModel)


@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    """Module-level singleton; the Anthropic SDK is thread-safe."""
    settings = get_settings()
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.anthropic_timeout_s,
    )


def _extract_text(message: anthropic.types.Message) -> str:
    parts: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)  # type: ignore[attr-defined]
    return "".join(parts)


def analyze(
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    response_model: type[R],
    settings: Settings | None = None,
) -> R:
    """Call the Anthropic API requesting a JSON response matching `response_model`.

    The system prompt is sent with `cache_control: ephemeral` so repeated calls
    against the same endpoint share a cached prefix. Volatile data (the user
    payload) lives strictly after the cache breakpoint.
    """
    cfg = settings or get_settings()
    client = get_client()

    schema = response_model.model_json_schema()

    try:
        message = client.messages.create(
            model=cfg.anthropic_model,
            max_tokens=cfg.anthropic_max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(user_payload, separators=(",", ":")),
                }
            ],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": schema,
                }
            },
        )
    except anthropic.AuthenticationError as exc:
        logger.error("Anthropic auth failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LLM authentication failed.",
        ) from exc
    except anthropic.RateLimitError as exc:
        logger.warning("Anthropic rate-limited: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="LLM rate limit exceeded; retry shortly.",
        ) from exc
    except anthropic.BadRequestError as exc:
        logger.error("Anthropic bad request: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM rejected the request: {exc.message}",
        ) from exc
    except anthropic.APIError as exc:
        logger.error("Anthropic API error %s: %s", getattr(exc, "status_code", "?"), exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM upstream error.",
        ) from exc

    if message.stop_reason == "refusal":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="LLM declined to produce a response for this input.",
        )

    raw = _extract_text(message).strip()
    if not raw:
        logger.error("Empty response from Anthropic (stop_reason=%s)", message.stop_reason)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned an empty response.",
        )

    try:
        return response_model.model_validate_json(raw)
    except ValidationError as exc:
        logger.error("LLM output failed schema validation. Raw: %s", raw[:500])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM output did not match expected schema: {exc.errors()[:3]}",
        ) from exc
