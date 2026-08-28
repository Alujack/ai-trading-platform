"""Structured settings for the trading backend.

Every knob the Express API read from `process.env` is declared here so the
service fails fast on a missing/mistyped value instead of silently defaulting
deep inside a request. Names match the existing environment contract exactly —
the migration changes the runtime, not the operator's `.env`.
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _truthy(raw: str | None) -> bool | None:
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseSettings):
    """Environment-driven configuration for `services/backend`."""

    # ---- Server ----
    backend_host: str = Field(default="0.0.0.0")
    backend_port: int = Field(default=8000)
    log_level: str = Field(default="info")
    node_env: str = Field(default="development")

    # ---- Persistence ----
    database_url: str = Field(default="postgresql://postgres:postgres@localhost:5432/trading")
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    redis_url: str = Field(default="redis://localhost:6379")

    # ---- Job ownership (exactly one process runs the schedulers) ----
    backend_job_owner: bool = Field(default=True)
    api_shadow_mode: bool = Field(default=False)
    api_migration_lock_key: str = Field(default="migration:execution-owner")

    enable_paper_trading: bool = Field(default=False)
    enable_weekly_review: bool = Field(default=False)
    enable_daily_briefing: bool = Field(default=True)
    enable_news_brief: bool = Field(default=True)
    enable_scalp_manager: bool = Field(default=False)

    # ---- Paper account state (unchanged contract) ----
    paper_user_id: str = Field(default="system")
    paper_account_balance: float = Field(default=10_000.0)
    paper_peak_balance: float = Field(default=10_000.0)
    paper_risk_percent: float = Field(default=1.0)
    paper_max_open_trades: int = Field(default=5)
    max_trades_per_day: int = Field(default=1)
    daily_profit_target_pct: float = Field(default=2.0)

    # ---- Secrets ----
    encryption_key: str | None = Field(default=None)

    # ---- Broker ----
    broker: str = Field(default="paper")
    exness_env: str = Field(default="demo")
    mt5_bridge_url: str | None = Field(default=None)
    mt5_bridge_token: str | None = Field(default=None)
    broker_symbol_map: str | None = Field(default=None)
    default_deviation: int = Field(default=20)

    # ---- Scalp manager ----
    scalp_managed_prefix: str = Field(default="scalp")
    scalp_min_stop_ratio: float = Field(default=0.5)
    scalp_emergency_r: float = Field(default=0.8)
    scalp_watch_r: float = Field(default=0.5)
    scalp_trail_start_r: float = Field(default=1.0)
    scalp_trail_giveback_r: float = Field(default=0.5)

    # ---- Telegram ----
    telegram_bot_token: str | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)
    telegram_webhook_secret: str | None = Field(default=None)
    telegram_allowed_user_ids: str | None = Field(default=None)
    telegram_overrides_path: str | None = Field(default=None)

    # ---- Feature-flag env defaults ----
    raw_signal_feed: bool | None = Field(default=None)

    # ---- Backtests ----
    backtest_python: str | None = Field(default=None)
    backtest_data_dir: str | None = Field(default=None)
    backtest_timeout_s: float = Field(default=180.0)

    # ---- AI provider overrides file (integrations/ai) ----
    ai_provider_overrides_path: str | None = Field(default=None)

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("broker", "exness_env", mode="before")
    @classmethod
    def _lower_strip(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    # --- derived helpers ---

    @property
    def is_live_broker(self) -> bool:
        """True when BROKER=exness (live MT5 execution)."""
        return self.broker == "exness"

    @property
    def sqlalchemy_url(self) -> str:
        """`DATABASE_URL` rewritten for the asyncpg driver.

        The env var is shared with Prisma/asyncpg callers, which use the bare
        `postgresql://` scheme; SQLAlchemy needs the driver spelled out.
        """
        url = self.database_url
        if url.startswith("postgresql+"):
            return url
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url[len("postgres://") :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor; safe as a FastAPI dependency."""
    s = Settings()  # type: ignore[call-arg]
    # ENABLE_DAILY_BRIEFING / ENABLE_NEWS_BRIEF are "on unless explicitly
    # false" in the legacy Express entrypoint, unlike the other ENABLE_* flags.
    # Re-read those two so an unset var keeps the old behaviour.
    for name, env_key in (
        ("enable_daily_briefing", "ENABLE_DAILY_BRIEFING"),
        ("enable_news_brief", "ENABLE_NEWS_BRIEF"),
    ):
        explicit = _truthy(os.environ.get(env_key))
        object.__setattr__(s, name, True if explicit is None else explicit)
    return s
