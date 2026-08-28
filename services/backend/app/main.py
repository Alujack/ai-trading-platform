"""FastAPI application entrypoint for the trading backend.

Owns the whole trading domain: PostgreSQL and Redis access, the signal gate, the
authoritative risk engine, paper/live execution, AI orchestration, Telegram and
broker integrations, SSE, and the scheduled jobs. Next.js in front of it is a
thin BFF with no database, broker or LLM credentials.

Startup mirrors the Express `index.ts`: connect Redis, push the broker session
when live, start the schedulers this process owns, and recompute the daily
briefing once (without pinging Telegram). Shutdown stops the jobs before closing
the pools so no tick is cut off mid-transaction.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.errors import install_error_handlers
from .api.routes import install_routes
from .core.logging import configure_logging
from .core.settings import get_settings
from .db.redis_client import close_redis, ping_redis
from .db.session import dispose_engine, ping_db
from .jobs.scheduler import run_daily_briefing_once, start_schedulers, stop_schedulers

load_dotenv()
log = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = get_settings()
    configure_logging(cfg.log_level)

    if cfg.api_shadow_mode:
        log.warning(
            "API_SHADOW_MODE=true — decisions are computed and reported, "
            "but no signal, trade, Telegram message or broker order is written"
        )

    if not await ping_db():
        # Not fatal: readiness reports it and the pool retries per request, so a
        # cold Postgres on `docker compose up` doesn't kill the container.
        log.error("[startup] Postgres not reachable yet — /health/ready will report it")
    if not await ping_redis():
        log.error("[startup] Redis not reachable yet — caching/realtime degraded")

    # When live (BROKER=exness), push the UI-configured MT5 credentials to the
    # bridge so the terminal is logged into the right account on boot.
    # Best-effort: a missing/failed login is logged, not fatal.
    if cfg.is_live_broker and not cfg.api_shadow_mode:
        try:
            from .db.session import session_scope
            from .domain.execution.broker import ensure_broker_session

            async with session_scope() as session:
                result = await ensure_broker_session(session)
            log.info("[broker] startup session: ok=%s %s", result["ok"], result["detail"])
        except Exception as exc:
            log.error("[broker] startup session failed: %s", exc)

    start_schedulers()

    # Daily briefing: the agent's morning routine. Recompute once on startup so a
    # restart has a fresh summary, then the 06:00 UTC cron takes over.
    if cfg.enable_daily_briefing and cfg.backend_job_owner and not cfg.api_shadow_mode:
        try:
            await run_daily_briefing_once()
        except Exception as exc:
            log.error("[startup] daily briefing failed: %s", exc)

    log.info("backend ready on %s:%s", cfg.backend_host, cfg.backend_port)
    try:
        yield
    finally:
        log.info("[shutdown] stopping schedulers")
        stop_schedulers()
        await dispose_engine()
        await close_redis()
        log.info("[shutdown] complete")


def create_app() -> FastAPI:
    cfg = get_settings()
    configure_logging(cfg.log_level)

    app = FastAPI(
        title="AI Trading Backend",
        version="1.0.0",
        description=(
            "Trading-domain API: signal gate, risk engine, execution, AI, "
            "Telegram/broker integrations, realtime and scheduled jobs."
        ),
        lifespan=lifespan,
    )

    # The browser reaches this service through the Next.js BFF (same-origin), so
    # CORS is only here for the direct-call transition window and local tooling.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_routes(app)
    install_error_handlers(app)
    return app


app = create_app()
