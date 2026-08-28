"""Morning news brief → Telegram — port of `execution/newsBrief.ts`.

Part of the agent's "check news first" daily routine: before the session,
summarize the high/medium-impact economic events in the next 24h and push them
to the user's Telegram chat.

Best-effort throughout: a missing AI provider, empty calendar, or unconfigured
Telegram must never raise into the scheduler. The same `NewsEvent` rows feed the
risk engine's ±news-window block, so this brief and the trade gate stay in sync.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.serialization import iso
from ...db.enums import Impact
from ...db.models import NewsEvent
from ...integrations.ai import client as ai
from ...integrations.ai.schemas import NewsSummaryRequest
from ...integrations.telegram.client import (
    default_chat_id,
    esc,
    is_configured,
    send_message,
)
from ...jobs.clock import naive_utcnow
from .daily_briefing import collect_market_context

log = logging.getLogger("backend.newsBrief")

DAY = timedelta(days=1)


async def _ai_summary(events: list[NewsEvent]) -> str | None:
    """A short consolidated "what to watch" paragraph, or None on any failure."""
    try:
        headlines = [
            {
                "title": e.title,
                "source": "economic-calendar",
                "publishedAt": iso(e.scheduledAt),
                "body": (
                    f"{e.impact.value}-impact {e.currency} event. "
                    f"Forecast: {e.forecast or 'n/a'}, Previous: {e.previous or 'n/a'}."
                ),
            }
            for e in events[:25]
        ]
        result = await ai.news_summary(
            NewsSummaryRequest.model_validate({"headlines": headlines})
        )
        summary = (result.summary or "").strip()
        return summary or None
    except Exception as exc:  # noqa: BLE001
        log.warning("[newsBrief] AI summary unavailable: %s", exc)
        return None


async def _desk_call_section(session: AsyncSession) -> list[str]:
    """Morning desk call: bias + levels + risks per traded series."""
    contexts = await collect_market_context(session)
    if not contexts:
        return []
    lines = ["", "<b>DESK CALL</b>"]
    for m in contexts:
        dot = "🟢" if m["bias"] == "Bullish" else "🔴" if m["bias"] == "Bearish" else "⚪"
        lines.append(
            f"{dot} <b>{esc(m['symbol'])} {esc(m['timeframe'])}</b> — {esc(m['bias'])}"
        )
        lines.append(esc(m["summary"][:300]))
        if m["keyLevels"]:
            lines.append(f"Levels: {esc(' · '.join(m['keyLevels'][:3]))}")
        if m["risks"]:
            lines.append(f"Risks: {esc(' · '.join(m['risks'][:2]))}")
    return lines


async def build_news_brief(session: AsyncSession, now: datetime | None = None) -> str:
    """The HTML message body for the next-24h news brief."""
    reference = now or naive_utcnow()
    events = (
        (
            await session.execute(
                select(NewsEvent)
                .where(
                    NewsEvent.scheduledAt > reference,
                    NewsEvent.scheduledAt < reference + DAY,
                    NewsEvent.impact.in_([Impact.HIGH, Impact.MEDIUM]),
                )
                .order_by(NewsEvent.scheduledAt.asc())
                .limit(15)
            )
        )
        .scalars()
        .all()
    )

    desk = await _desk_call_section(session)

    if not events:
        return "\n".join(
            [
                "📰 <b>NEWS BRIEF</b> — next 24h (UTC)",
                "",
                "No high- or medium-impact events scheduled. Clear to follow your "
                "plan — still read the chart before any entry.",
                *desk,
            ]
        )

    summary = await _ai_summary(events)
    lines = ["📰 <b>NEWS BRIEF</b> — next 24h (UTC)", ""]
    if summary:
        lines += [esc(summary), ""]

    lines.append("<b>SCHEDULED</b>")
    for e in events:
        time = iso(e.scheduledAt)[11:16]  # HH:MM UTC
        flag = "🔴" if e.impact == Impact.HIGH else "🟠"
        lines.append(f"{flag} {time} <b>{esc(e.currency)}</b> — {esc(e.title)}")

    lines += desk
    lines += ["", "⚠️ Trades auto-block ±30m around high-impact events."]
    return "\n".join(lines)


async def send_daily_news_brief(session: AsyncSession) -> dict[str, object]:
    """Build and send the morning brief. No-ops when Telegram is not configured."""
    if not is_configured():
        return {"sent": False, "reason": "telegram_not_configured"}
    chat_id = default_chat_id()
    if not chat_id:
        return {"sent": False, "reason": "no_chat_id"}

    text = await build_news_brief(session)
    message_id = await send_message(chat_id, text)
    return {"sent": True} if message_id is not None else {"sent": False, "reason": "send_failed"}
