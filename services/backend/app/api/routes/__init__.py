"""Route registry. Order matters where a literal path shadows a param route."""
from __future__ import annotations

from fastapi import FastAPI

from . import (
    analyze,
    backtests,
    brokers,
    candles,
    config,
    health,
    journal,
    market_context,
    news,
    news_alert,
    performance,
    positions,
    providers,
    realtime,
    signals,
    symbols,
    telegram,
)

#: Registration order mirrors `apps/api/src/routes/index.ts`.
ROUTERS = (
    health.router,
    candles.router,
    symbols.router,
    signals.router,
    performance.router,
    positions.router,
    journal.router,
    market_context.router,
    providers.router,
    news.router,
    news_alert.router,
    realtime.router,
    config.router,
    telegram.router,
    backtests.router,
    brokers.router,
    analyze.router,
)


def install_routes(app: FastAPI) -> None:
    for router in ROUTERS:
        app.include_router(router)
