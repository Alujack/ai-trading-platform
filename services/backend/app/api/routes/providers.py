"""AI provider configuration — port of `routes/aiProvider.routes.ts`.

Phase 3 removes the proxy: these routes now read and mutate the provider state
in this process instead of forwarding to `services/ai`. The wire contract is
unchanged, including the `/api/ai-provider*` paths and the redaction rules — a
raw key is never returned, only presence flags and a hint.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ...integrations.ai.providers import (
    available,
    clear_key,
    get_active,
    provider_details,
    set_active,
    set_key,
    test_provider,
)
from ...integrations.ai.settings import get_settings as get_ai_settings
from ..errors import HttpError

router = APIRouter(tags=["ai"])


class SetProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=40)


class SetKeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=40)
    apiKey: str = Field(min_length=1, max_length=400)
    model: str | None = Field(default=None, max_length=120)


class ProviderOnlyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=40)


def _state() -> dict[str, Any]:
    cfg = get_ai_settings()
    return {
        "active": get_active(cfg),
        "available": available(cfg),
        "providers": provider_details(cfg),
    }


@router.get("/api/ai-provider")
async def get_provider() -> dict[str, Any]:
    """Current provider + per-provider config status (depends on configured keys)."""
    return _state()


@router.put("/api/ai-provider")
async def put_provider(body: SetProviderBody) -> dict[str, Any]:
    """Switch the active provider at runtime."""
    cfg = get_ai_settings()
    try:
        set_active(body.provider, cfg)
    except ValueError as exc:
        raise HttpError(
            400,
            f"Provider '{body.provider}' is not available. Choose one of {available(cfg)}.",
        ) from exc
    return _state()


@router.put("/api/ai-provider/key")
async def put_provider_key(body: SetKeyBody) -> dict[str, Any]:
    """Save an API key (and optional model) for a provider — pasted from the UI."""
    try:
        set_key(body.provider, body.apiKey, body.model)
    except ValueError as exc:
        raise HttpError(400, f"'{body.provider}' does not accept a key.") from exc
    return _state()


@router.delete("/api/ai-provider/key")
async def delete_provider_key(body: ProviderOnlyBody) -> dict[str, Any]:
    """Remove a UI-set key for a provider (env keys, if any, remain)."""
    clear_key(body.provider)
    return _state()


@router.post("/api/ai-provider/test")
async def post_provider_test(body: ProviderOnlyBody) -> dict[str, Any]:
    """Verify a provider's key with one tiny live call."""
    import asyncio

    return await asyncio.to_thread(test_provider, body.provider)
