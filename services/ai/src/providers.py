"""Pluggable AI providers with a runtime-switchable active selection.

Three providers:
  * mock      — always available; canned, self-labeled output (no key needed).
  * anthropic — Claude, available when ANTHROPIC_API_KEY is set.
  * gemini    — Google Gemini, available when GEMINI_API_KEY is set.

The active provider is process-global and can be flipped at runtime via
`set_active()` (wired to POST /provider). `analyze()` dispatches to it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol, TypeVar

from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError

from .settings import Settings, get_settings

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=BaseModel)

MOCK_TAG = "[MOCK AI — switch the provider toggle to Claude or Gemini for real analysis] "


class Provider(Protocol):
    name: str

    def analyze(
        self, *, system_prompt: str, user_payload: dict[str, Any], response_model: type[R]
    ) -> R: ...


# --------------------------------------------------------------------------- #
# Mock                                                                        #
# --------------------------------------------------------------------------- #
class MockProvider:
    name = "mock"

    def analyze(self, *, system_prompt, user_payload, response_model):  # type: ignore[no-untyped-def]
        model = response_model.__name__
        if model == "MarketContextResponse":
            data: dict[str, Any] = {
                "bias": "Neutral",
                "summary": MOCK_TAG
                + "Price is consolidating near the EMA stack with no decisive trend; "
                "RSI is mid-range and ATR is average. Wait for a break of the noted levels.",
                "keyLevels": [
                    "Prior session high — first resistance",
                    "EMA50 confluence — pivot",
                    "Prior swing low — support",
                ],
                "risks": ["High-impact calendar event this week", "Thin off-session liquidity"],
            }
        elif model == "ValidateSignalResponse":
            data = {
                "score": 78,
                "approved": True,
                "reasoning": MOCK_TAG + "Setup aligns with the EMA trend and RR is acceptable.",
                "concerns": ["Mock response — not a real model assessment"],
            }
        elif model == "JournalReviewResponse":
            data = {
                "patterns": [MOCK_TAG + "Most losses cluster in low-volatility chop."],
                "strengths": ["Consistent position sizing"],
                "weaknesses": ["Exits a touch early on winners"],
                "suggestions": ["Let winners run to the planned target before scaling out"],
            }
        elif model == "NewsSummaryResponse":
            data = {
                "summary": MOCK_TAG + "Headlines summarized without a live model.",
                "impact": "LOW",
                "currency": "USD",
                "rationale": "Mock response — impact not assessed by a real model.",
            }
        else:  # pragma: no cover - defensive
            raise HTTPException(status_code=500, detail=f"Mock has no shape for {model}")
        return response_model.model_validate(data)


# --------------------------------------------------------------------------- #
# Anthropic (Claude)                                                          #
# --------------------------------------------------------------------------- #
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key, timeout=settings.request_timeout_s
        )
        self._model = settings.anthropic_model
        self._max_tokens = settings.max_output_tokens

    def analyze(self, *, system_prompt, user_payload, response_model):  # type: ignore[no-untyped-def]
        anthropic = self._anthropic
        schema = response_model.model_json_schema()
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": json.dumps(user_payload, separators=(",", ":"))}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.AuthenticationError as exc:
            logger.error("Anthropic auth failed: %s", exc)
            raise HTTPException(status_code=500, detail="Claude authentication failed.") from exc
        except anthropic.RateLimitError as exc:
            raise HTTPException(status_code=429, detail="Claude rate limit; retry shortly.") from exc
        except anthropic.APIError as exc:
            logger.error("Anthropic API error: %s", exc)
            raise HTTPException(status_code=502, detail="Claude upstream error.") from exc

        if getattr(message, "stop_reason", None) == "refusal":
            raise HTTPException(status_code=422, detail="Claude declined to respond.")
        raw = "".join(
            b.text for b in message.content if getattr(b, "type", None) == "text"
        ).strip()
        return _validate(raw, response_model, "Claude")


# --------------------------------------------------------------------------- #
# Gemini                                                                      #
# --------------------------------------------------------------------------- #
# Gemini's response_schema accepts only this OpenAPI subset; Pydantic's JSON
# schema carries extras (additionalProperties, title, minimum/…) that it rejects.
# We strip to the structural keys and let Pydantic re-validate the output.
_GEMINI_SCHEMA_KEYS = {"type", "properties", "required", "items", "enum", "nullable", "description"}


def _to_gemini_schema(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key not in _GEMINI_SCHEMA_KEYS:
                continue
            if key == "properties":
                out[key] = {k: _to_gemini_schema(v) for k, v in value.items()}
            elif key == "items":
                out[key] = _to_gemini_schema(value)
            else:
                out[key] = value
        return out
    return node


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai

        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        self._max_tokens = settings.max_output_tokens

    def analyze(self, *, system_prompt, user_payload, response_model):  # type: ignore[no-untyped-def]
        from google.genai import errors as genai_errors
        from google.genai import types

        schema = _to_gemini_schema(response_model.model_json_schema())
        try:
            resp = self._client.models.generate_content(
                model=self._model,
                contents=json.dumps(user_payload, separators=(",", ":")),
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=schema,
                    max_output_tokens=self._max_tokens,
                    temperature=0.2,
                ),
            )
        except genai_errors.ClientError as exc:
            code = getattr(exc, "code", None)
            if code == 429:
                raise HTTPException(status_code=429, detail="Gemini rate limit; retry shortly.") from exc
            if code in (401, 403):
                raise HTTPException(status_code=500, detail="Gemini authentication failed.") from exc
            logger.error("Gemini client error %s: %s", code, exc)
            raise HTTPException(status_code=502, detail=f"Gemini rejected the request: {exc}") from exc
        except genai_errors.ServerError as exc:
            logger.error("Gemini server error: %s", exc)
            raise HTTPException(status_code=502, detail="Gemini upstream error.") from exc
        except Exception as exc:  # noqa: BLE001 — e.g. schema conversion issues
            logger.error("Gemini call failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"Gemini call failed: {exc}") from exc

        raw = (resp.text or "").strip()
        return _validate(raw, response_model, "Gemini")


def _validate(raw: str, response_model: type[R], label: str) -> R:
    if not raw:
        raise HTTPException(status_code=502, detail=f"{label} returned an empty response.")
    try:
        return response_model.model_validate_json(raw)
    except ValidationError as exc:
        logger.error("%s output failed schema validation. Raw: %s", label, raw[:500])
        raise HTTPException(
            status_code=502, detail=f"{label} output did not match schema: {exc.errors()[:3]}"
        ) from exc


# --------------------------------------------------------------------------- #
# Registry + runtime state                                                    #
# --------------------------------------------------------------------------- #
_BUILDERS = {
    "mock": lambda _s: MockProvider(),
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}

# Display labels + which env field / model field each provider maps to. Adding a
# new key-based provider is just another entry here plus a builder above.
PROVIDER_META: dict[str, dict[str, str]] = {
    "mock": {"label": "Mock", "key_field": "", "model_field": ""},
    "anthropic": {
        "label": "Claude",
        "key_field": "anthropic_api_key",
        "model_field": "anthropic_model",
    },
    "gemini": {
        "label": "Gemini",
        "key_field": "gemini_api_key",
        "model_field": "gemini_model",
    },
}

_active: str | None = None
_cache: dict[str, Provider] = {}

# Runtime key/model overrides set from the UI. Persisted to a gitignored file so
# pasted keys survive a restart (same trust level as .env — never committed).
_OVERRIDES_PATH = Path(__file__).resolve().parent.parent / ".provider_overrides.json"
_overrides: dict[str, dict[str, str]] = {}


def _load_overrides() -> None:
    global _overrides
    try:
        if _OVERRIDES_PATH.exists():
            _overrides = json.loads(_OVERRIDES_PATH.read_text("utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — bad file shouldn't block startup
        logger.warning("could not read provider overrides: %s", exc)
        _overrides = {}


def _save_overrides() -> None:
    try:
        _OVERRIDES_PATH.write_text(json.dumps(_overrides, indent=2), "utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist provider overrides: %s", exc)


_load_overrides()


def _effective_settings(name: str, cfg: Settings) -> Settings:
    """Settings with any UI-pasted key/model for `name` layered on top of env."""
    ov = _overrides.get(name)
    meta = PROVIDER_META.get(name, {})
    if not ov or not meta:
        return cfg
    update: dict[str, Any] = {}
    if ov.get("api_key") and meta.get("key_field"):
        update[meta["key_field"]] = ov["api_key"]
    if ov.get("model") and meta.get("model_field"):
        update[meta["model_field"]] = ov["model"]
    return cfg.model_copy(update=update) if update else cfg


def _key_for(name: str, cfg: Settings) -> str | None:
    """Effective API key for a provider: UI override wins, else env."""
    ov = _overrides.get(name, {})
    if ov.get("api_key"):
        return ov["api_key"]
    meta = PROVIDER_META.get(name, {})
    field = meta.get("key_field")
    return getattr(cfg, field, None) if field else None


def available(settings: Settings | None = None) -> list[str]:
    cfg = settings or get_settings()
    out = ["mock"]
    for name in ("anthropic", "gemini"):
        if _key_for(name, cfg):
            out.append(name)
    return out


def _default(settings: Settings) -> str:
    avail = available(settings)
    if settings.default_provider in avail:
        return settings.default_provider
    # "auto": prefer a configured real provider over mock.
    for name in ("gemini", "anthropic"):
        if name in avail:
            return name
    return "mock"


def get_active(settings: Settings | None = None) -> str:
    global _active
    cfg = settings or get_settings()
    if _active is None:
        _active = _default(cfg)
    return _active


def set_active(name: str, settings: Settings | None = None) -> str:
    global _active
    cfg = settings or get_settings()
    if name not in available(cfg):
        raise ValueError(name)
    _active = name
    logger.info("active provider set to %s", name)
    return _active


def _effective_model(name: str, cfg: Settings) -> str | None:
    meta = PROVIDER_META.get(name, {})
    field = meta.get("model_field")
    if not field:
        return None
    return getattr(_effective_settings(name, cfg), field, None)


def provider_details(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Per-provider status for the settings UI. Never returns the raw key."""
    cfg = settings or get_settings()
    active = get_active(cfg)
    avail = available(cfg)
    out: list[dict[str, Any]] = []
    for name, meta in PROVIDER_META.items():
        key = _key_for(name, cfg)
        needs_key = bool(meta.get("key_field"))
        ov = _overrides.get(name, {})
        out.append(
            {
                "name": name,
                "label": meta["label"],
                "needsKey": needs_key,
                "hasKey": bool(key) or not needs_key,
                "keyHint": (f"…{key[-4:]}" if key and len(key) >= 4 else None),
                "keySource": ("ui" if ov.get("api_key") else ("env" if key else None)),
                "model": _effective_model(name, cfg),
                "configured": name in avail,
                "active": name == active,
            }
        )
    return out


def set_key(name: str, api_key: str, model: str | None = None, settings: Settings | None = None) -> None:
    """Store a UI-pasted key (and optional model) for a provider; rebuild it."""
    if name not in PROVIDER_META or not PROVIDER_META[name].get("key_field"):
        raise ValueError(name)
    entry = _overrides.get(name, {})
    if api_key:
        entry["api_key"] = api_key.strip()
    if model:
        entry["model"] = model.strip()
    _overrides[name] = entry
    _cache.pop(name, None)  # force rebuild with the new key
    _save_overrides()
    logger.info("provider %s key updated via UI", name)


def clear_key(name: str) -> None:
    _overrides.pop(name, None)
    _cache.pop(name, None)
    _save_overrides()
    logger.info("provider %s UI key cleared", name)
    # If the now-unconfigured provider was active, fall back.
    global _active
    if _active == name and name not in available():
        _active = _default(get_settings())


def test_provider(name: str, settings: Settings | None = None) -> dict[str, Any]:
    """Make one tiny real call to verify a provider's key works."""
    from .schemas import NewsSummaryResponse

    cfg = settings or get_settings()
    if name not in PROVIDER_META:
        return {"ok": False, "detail": f"Unknown provider '{name}'."}
    if name != "mock" and not _key_for(name, cfg):
        return {"ok": False, "detail": "No API key configured for this provider."}
    try:
        provider = _BUILDERS[name](_effective_settings(name, cfg))
        provider.analyze(
            system_prompt="Reply with a single short test summary.",
            user_payload={"headlines": [{"title": "Connectivity test"}]},
            response_model=NewsSummaryResponse,
        )
        return {"ok": True, "detail": f"{PROVIDER_META[name]['label']} responded successfully."}
    except HTTPException as exc:
        return {"ok": False, "detail": str(exc.detail)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _get_provider(name: str, settings: Settings) -> Provider:
    if name not in _cache:
        _cache[name] = _BUILDERS[name](_effective_settings(name, settings))
    return _cache[name]


def analyze(
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    response_model: type[R],
    settings: Settings | None = None,
) -> R:
    cfg = settings or get_settings()
    name = get_active(cfg)
    try:
        provider = _get_provider(name, cfg)
    except Exception as exc:  # noqa: BLE001 — surface construction failures as 502
        logger.error("failed to build provider %s: %s", name, exc)
        raise HTTPException(status_code=502, detail=f"Provider '{name}' unavailable: {exc}") from exc
    return provider.analyze(
        system_prompt=system_prompt, user_payload=user_payload, response_model=response_model
    )
