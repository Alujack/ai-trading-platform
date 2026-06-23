"""Runtime settings for the AI analysis service.

Keys are optional: the service always boots with the built-in `mock` provider,
and Anthropic / Gemini become selectable only when their key is configured.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration."""

    # --- Anthropic (Claude) ---
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key.")
    anthropic_model: str = Field(default="claude-sonnet-4-6")

    # --- Google (Gemini) ---
    gemini_api_key: str | None = Field(default=None, description="Google Gemini API key.")
    gemini_model: str = Field(default="gemini-2.5-flash")

    # --- Shared generation knobs ---
    max_output_tokens: int = Field(default=4096, ge=512, le=16000, alias="anthropic_max_tokens")
    request_timeout_s: float = Field(default=60.0, gt=0, alias="anthropic_timeout_s")

    # Preferred provider on startup. "auto" picks a configured real provider,
    # falling back to "mock". May be flipped at runtime via POST /provider.
    default_provider: str = Field(default="auto")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor; safe for FastAPI dependency injection."""
    return Settings()  # type: ignore[call-arg]
