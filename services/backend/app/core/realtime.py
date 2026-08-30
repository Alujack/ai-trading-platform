"""Realtime event fan-out over Redis pub/sub — port of `lib/realtime.ts`.

Best-effort by contract: a Redis hiccup must never break the caller (signal
creation, candle ingest), so failures are logged and swallowed.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Literal

from ..db.redis_client import RT_CHANNEL, publish

log = logging.getLogger("backend.rt")

RtType = Literal["candle", "signal", "trade", "news"]


async def publish_event(
    type_: RtType | str,
    symbol: str | None = None,
    timeframe: str | None = None,
    candle: dict[str, object] | None = None,
) -> None:
    """Fan one event out to every connected SSE client."""
    event: dict[str, object] = {"type": type_, "at": int(time.time() * 1000)}
    if symbol is not None:
        event["symbol"] = symbol
    if timeframe is not None:
        event["timeframe"] = timeframe
    if candle is not None:
        event["candle"] = candle
    await publish(RT_CHANNEL, json.dumps(event))
