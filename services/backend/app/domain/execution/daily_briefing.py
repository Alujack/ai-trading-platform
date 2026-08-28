"""The agent's morning routine — port of `execution/dailyBriefing.ts`.

Summarizes how the desk has been trading before the day starts: performance +
expectancy, the grades/lessons from recently closed trades, and the high-impact
news ahead. Persists `briefing:latest` to Redis for the dashboard. Best-effort:
never raises into the startup path.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.serialization import iso, num_or_none
from ...db.enums import Impact, TradeStatus
from ...db.models import NewsEvent, Trade
from ...db.redis_client import cache_set
from ...jobs.clock import naive_utcnow
from ..performance.metrics import TradeStats, compute_performance
from .data_freshness import traded_pairs

log = logging.getLogger("backend.briefing")

DAY = timedelta(days=1)


async def collect_market_context(session: AsyncSession) -> list[dict[str, Any]]:
    """Market context for every traded series — best-effort, never raises."""
    from ..market_context import get_market_context

    out: list[dict[str, Any]] = []
    try:
        for pair in await traded_pairs(session):
            try:
                out.append(
                    await get_market_context(session, pair["symbol"], pair["timeframe"])
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[dailyBriefing] market context unavailable for %s/%s: %s",
                    pair["symbol"],
                    pair["timeframe"],
                    exc,
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("[dailyBriefing] market context skipped: %s", exc)
    return out


async def run_daily_briefing(session: AsyncSession) -> dict[str, Any]:
    """Build the briefing, cache it in Redis, and log the one-line summary."""
    now = naive_utcnow()
    since_24h = now - DAY

    closed = (
        (
            await session.execute(
                select(Trade)
                .where(Trade.status == TradeStatus.CLOSED)
                .order_by(Trade.closedAt.desc())
            )
        )
        .scalars()
        .all()
    )

    stats = [
        TradeStats(
            entryPrice=float(t.entryPrice),
            exitPrice=num_or_none(t.exitPrice),
            profitLoss=num_or_none(t.profitLoss),
            direction=t.signal.direction.value,
            stopLoss=float(t.signal.stopLoss),
        )
        for t in closed
    ]
    perf = compute_performance(stats)

    # R-expectancy + grade distribution from the per-trade journal reviews.
    rmults: list[float] = []
    grade_distribution: dict[str, int] = {}
    latest_journal: dict[str, Any] = {}
    for t in closed:
        journal = max(t.journals, key=lambda j: j.createdAt) if t.journals else None
        latest_journal[t.id] = journal
        if journal is None:
            continue
        if journal.rMultiple is not None:
            rmults.append(float(journal.rMultiple))
        if journal.grade:
            grade_distribution[journal.grade] = grade_distribution.get(journal.grade, 0) + 1
    r_expectancy = sum(rmults) / len(rmults) if rmults else 0.0

    recent_24h = []
    for t in closed:
        if t.closedAt is None or t.closedAt < since_24h:
            continue
        journal = latest_journal.get(t.id)
        recent_24h.append(
            {
                "symbol": t.signal.symbol,
                "direction": t.signal.direction.value,
                "outcome": getattr(journal, "outcome", None),
                "grade": getattr(journal, "grade", None),
                "rMultiple": (
                    float(journal.rMultiple)
                    if journal is not None and journal.rMultiple is not None
                    else None
                ),
                "lesson": getattr(journal, "lesson", None),
                "closedAt": iso(t.closedAt),
            }
        )

    news = (
        (
            await session.execute(
                select(NewsEvent)
                .where(
                    NewsEvent.scheduledAt > now,
                    NewsEvent.scheduledAt < now + DAY,
                    NewsEvent.impact == Impact.HIGH,
                )
                .order_by(NewsEvent.scheduledAt.asc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )

    top_lessons = [
        journal.lesson
        for journal in (latest_journal.get(t.id) for t in closed)
        if journal is not None and journal.lesson
    ][:5]

    market_context = await collect_market_context(session)

    briefing = {
        "generatedAt": iso(now),
        "performance": {
            **perf,
            "rExpectancy": round(r_expectancy, 3),
            "gradedTrades": len(rmults),
        },
        "gradeDistribution": grade_distribution,
        "recent24h": recent_24h,
        "upcomingHighImpactNews": [
            {"title": n.title, "currency": n.currency, "scheduledAt": iso(n.scheduledAt)}
            for n in news
        ],
        "topLessons": top_lessons,
        "marketContext": market_context,
    }

    await cache_set("briefing:latest", json.dumps(briefing))

    context_summary = (
        ",".join(f"{m['symbol']}/{m['timeframe']}:{m['bias']}" for m in market_context)
        or "none"
    )
    log.info(
        "[dailyBriefing] %s trades=%s win=%s%% expectancy=$%s R=%s PF=%s recent24h=%d "
        "highImpactNews=%d marketContext=%s",
        iso(now),
        perf["totalTrades"],
        perf["winRate"],
        perf["expectancy"],
        briefing["performance"]["rExpectancy"],
        perf["profitFactor"],
        len(recent_24h),
        len(news),
        context_summary,
    )
    return briefing
