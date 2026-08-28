"""Data-freshness guard (alerting half) — port of `execution/dataFreshness.ts`.

The Python strategy runner refuses to TRADE a stale series
(`services/data/strategy_runner.py`); this daily check tells the operator THAT it
is refusing, via Telegram. The July 2026 ingestion outage ran unnoticed for weeks
precisely because the pipeline went quiet without complaint.

This alert matters more, not less, now that the raw-feed flag can surface
stale-series candidates for manual eyes: those rows are tagged "STALE DATA" and
can never execute, but the operator still needs to know the ingestion is broken.

Best-effort throughout: an unconfigured Telegram or an empty `Candle` table must
never raise into the scheduler.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import Candle, Strategy
from ...integrations.telegram.client import default_chat_id, is_configured, send_message
from ...jobs.clock import as_naive_utc, naive_utcnow

log = logging.getLogger("backend.freshness")

# Mirrors _STALE_AGE_LIMIT_S in services/data/strategy_runner.py: 2× the
# timeframe for intraday, 3 days for daily so a weekend can't false-alarm it.
STALE_LIMIT: dict[str, timedelta] = {
    "1min": timedelta(minutes=2),
    "5min": timedelta(minutes=10),
    "15min": timedelta(minutes=30),
    "60min": timedelta(hours=2),
    "daily": timedelta(days=3),
}

# FX/metals venues are closed Fri 22:00 → Sun 22:00 UTC, so intraday series
# legitimately freeze there. Crypto trades through and is exempt (mirrors
# _WEEKEND_OPEN_SYMBOLS in services/data/fetcher.py).
WEEKEND_OPEN_SYMBOLS = frozenset({"BTCUSD"})


@dataclass(slots=True)
class FreshnessIssue:
    symbol: str
    timeframe: str
    newestBar: datetime | None
    ageMinutes: int | None


def _limit_for(timeframe: str) -> timedelta:
    return STALE_LIMIT.get(timeframe, timedelta(hours=2))


def effective_now(now: datetime, symbol: str) -> datetime:
    """During the weekend gap, measure staleness from the Friday 22:00 UTC close.

    A healthy-but-closed market shouldn't page anyone.
    """
    if symbol in WEEKEND_OPEN_SYMBOLS:
        return now
    day = (now.weekday() + 1) % 7  # Python Mon=0 → JS Sun=0
    in_gap = (day == 5 and now.hour >= 22) or day == 6 or (day == 0 and now.hour < 22)
    if not in_gap:
        return now
    days_back_to_friday = 0 if day == 5 else 1 if day == 6 else 2
    friday_close = (now - timedelta(days=days_back_to_friday)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )
    return friday_close


async def traded_pairs(session: AsyncSession) -> list[dict[str, str]]:
    """(symbol, timeframe) pairs the desk actually trades.

    The union of every enabled strategy's params scoping. A strategy without
    explicit scoping widens the check to every distinct pair in the Candle table.
    """
    strategies = (
        (await session.execute(select(Strategy).where(Strategy.enabled.is_(True))))
        .scalars()
        .all()
    )
    if not strategies:
        return []
    pairs: dict[str, dict[str, str]] = {}
    need_fallback = False
    for s in strategies:
        params = s.params if isinstance(s.params, dict) else {}
        symbols = [
            x for x in (params.get("symbols") or []) if isinstance(x, str) and x.strip()
        ]
        timeframes = [
            x for x in (params.get("timeframes") or []) if isinstance(x, str) and x.strip()
        ]
        if not symbols or not timeframes:
            need_fallback = True
            continue
        for sym in symbols:
            for tf in timeframes:
                pairs[f"{sym}|{tf}"] = {"symbol": sym, "timeframe": tf}
    if need_fallback:
        rows = (
            await session.execute(select(distinct(Candle.symbol), Candle.timeframe))
        ).all()
        for symbol, timeframe in rows:
            pairs[f"{symbol}|{timeframe}"] = {"symbol": symbol, "timeframe": timeframe}
    return list(pairs.values())


async def check_data_freshness(
    session: AsyncSession, now: datetime | None = None
) -> list[FreshnessIssue]:
    """Every traded series whose newest bar is older than its staleness limit."""
    reference = as_naive_utc(now) if now else naive_utcnow()
    issues: list[FreshnessIssue] = []
    for pair in await traded_pairs(session):
        symbol, timeframe = pair["symbol"], pair["timeframe"]
        newest = (
            await session.execute(
                select(Candle.timestamp)
                .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if newest is None:
            issues.append(FreshnessIssue(symbol, timeframe, None, None))
            continue
        age = effective_now(reference, symbol) - newest
        if age > _limit_for(timeframe):
            issues.append(
                FreshnessIssue(
                    symbol, timeframe, newest, round(age.total_seconds() / 60)
                )
            )
    return issues


async def send_data_freshness_alert(session: AsyncSession) -> dict[str, object]:
    """Daily staleness alert → Telegram. No-ops when everything is fresh."""
    issues = await check_data_freshness(session)
    if not issues:
        return {"sent": False, "stale": 0, "reason": "all_fresh"}
    if not is_configured():
        return {"sent": False, "stale": len(issues), "reason": "telegram_not_configured"}
    chat_id = default_chat_id()
    if not chat_id:
        return {"sent": False, "stale": len(issues), "reason": "no_chat_id"}

    lines = ["🩸 <b>DATA STALE</b> — strategy scans are blocked on:", ""]
    for issue in issues:
        if issue.ageMinutes is None:
            age = "no candles at all"
        elif issue.ageMinutes >= 120:
            age = f"{round(issue.ageMinutes / 60)}h old"
        else:
            age = f"{issue.ageMinutes}m old"
        lines.append(f"• <b>{issue.symbol}</b> {issue.timeframe} — newest bar {age}")
    lines += ["", "Check the data worker / provider quota, then re-run ingestion."]

    message_id = await send_message(chat_id, "\n".join(lines))
    if message_id is not None:
        return {"sent": True, "stale": len(issues)}
    return {"sent": False, "stale": len(issues), "reason": "send_failed"}
