"""Runtime settings for the AI analysis service."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration."""

    anthropic_api_key: str = Field(..., description="Anthropic API key.")
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model ID used for analysis calls.",
    )
    anthropic_max_tokens: int = Field(default=4096, ge=512, le=16000)
    anthropic_timeout_s: float = Field(default=60.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor; safe for FastAPI dependency injection."""
    return Settings()  # type: ignore[call-arg]
