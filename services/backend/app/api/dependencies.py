"""Shared route dependencies and request-validation primitives.

The timeframe/status/impact literals mirror the Zod enums in
`apps/api/src/schemas/*`, so a bad value still yields the same 400 with the same
error body.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session

#: Labels match what `services/data` writes to `Candle.timeframe` (Twelve Data
#: interval naming). Keep in sync with TIMEFRAME_PERIOD_SECONDS in
#: services/data/main.py if either side changes.
Timeframe = Literal["1min", "5min", "15min", "60min", "daily"]
SignalStatusParam = Literal["PENDING", "ACTIVE", "CLOSED", "CANCELLED"]
RawVerdictParam = Literal["PENDING", "GENERATED", "REJECTED", "SKIPPED"]
ImpactParam = Literal["LOW", "MEDIUM", "HIGH"]
ScopeParam = Literal["GLOBAL", "STRATEGY", "SYMBOL"]
ModeParam = Literal["OFF", "AUTO", "CONFIRM"]
DirectionParam = Literal["LONG", "SHORT"]

Db = Annotated[AsyncSession, Depends(get_session)]


def is_blocked_only(value: str | None) -> bool:
    """Did the caller ask for blocked-only? (`"1"`/`"true"`, as in the Zod schema.)"""
    return value in ("1", "true")
