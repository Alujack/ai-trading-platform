"""One place that answers "what time is it" — every window in this platform is UTC.

The risk engine's news blackout, the daily breakers, and the freshness guard all
assume UTC end-to-end. Routing them through here keeps that assumption explicit
and gives tests a single seam to freeze.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def naive_utcnow() -> datetime:
    """Current UTC time as a naive datetime — the form the `TIMESTAMP(3)` columns hold."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def start_of_utc_day(now: datetime | None = None) -> datetime:
    """Midnight UTC of the given (or current) day, naive — matches `setUTCHours(0,0,0,0)`."""
    ref = now or utcnow()
    if ref.tzinfo is not None:
        ref = ref.astimezone(timezone.utc).replace(tzinfo=None)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0)


def as_naive_utc(value: datetime) -> datetime:
    """Normalize any datetime to the naive-UTC form the DB columns use."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def as_aware_utc(value: datetime) -> datetime:
    """Normalize any datetime to timezone-aware UTC (DB rows come back naive)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
