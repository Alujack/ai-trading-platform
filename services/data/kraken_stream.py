"""Realtime BTC/USD OHLC ingestion from Kraken Spot WebSocket v2.

Kraken publishes the full in-progress candle on trade events, so the worker can
upsert authoritative OHLCV rows instead of attempting to reconstruct candles
from sporadic REST polls. Updates are coalesced to one database flush per second
and fanned into the dashboard through the existing backend SSE channel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import websockets

from db import CandleRow, upsert_candles

log = logging.getLogger("data.kraken_stream")

KRAKEN_WS_URL = os.environ.get("KRAKEN_WS_URL", "wss://ws.kraken.com/v2")
SYMBOL = "BTCUSD"
KRAKEN_SYMBOL = "BTC/USD"
FLUSH_INTERVAL_S = float(os.environ.get("KRAKEN_FLUSH_INTERVAL_S", "1"))

_INTERVAL_TO_TIMEFRAME: dict[int, str] = {
    1: "1min",
    5: "5min",
    15: "15min",
    60: "60min",
    1440: "daily",
}
_TIMEFRAME_TO_INTERVAL = {tf: interval for interval, tf in _INTERVAL_TO_TIMEFRAME.items()}

Notify = Callable[
    [httpx.AsyncClient, str, str, str | None, dict[str, str] | None],
    Awaitable[None],
]


def _parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def candle_rows(message: dict[str, Any]) -> list[CandleRow]:
    """Translate one Kraken OHLC snapshot/update frame into database rows."""
    if message.get("channel") != "ohlc" or message.get("type") not in {"snapshot", "update"}:
        return []
    rows: list[CandleRow] = []
    for item in message.get("data") or []:
        if item.get("symbol") != KRAKEN_SYMBOL:
            continue
        timeframe = _INTERVAL_TO_TIMEFRAME.get(int(item.get("interval", 0)))
        if timeframe is None:
            continue
        rows.append(
            CandleRow(
                symbol=SYMBOL,
                timeframe=timeframe,
                timestamp=_parse_timestamp(str(item["interval_begin"])),
                open=Decimal(str(item["open"])),
                high=Decimal(str(item["high"])),
                low=Decimal(str(item["low"])),
                close=Decimal(str(item["close"])),
                volume=Decimal(str(item.get("volume") or 0)),
            )
        )
    return rows


def _subscription(timeframe: str) -> dict[str, Any]:
    interval = _TIMEFRAME_TO_INTERVAL[timeframe]
    return {
        "method": "subscribe",
        "params": {
            "channel": "ohlc",
            "symbol": [KRAKEN_SYMBOL],
            "interval": interval,
            "snapshot": True,
        },
        "req_id": interval,
    }


def _realtime_payload(row: CandleRow) -> dict[str, str]:
    timestamp = row.timestamp.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds")
    return {
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "timestamp": timestamp.replace("+00:00", "Z"),
        "open": str(row.open),
        "high": str(row.high),
        "low": str(row.low),
        "close": str(row.close),
        "volume": str(row.volume),
    }


async def _flush(
    pending: dict[str, CandleRow], client: httpx.AsyncClient, notify: Notify
) -> None:
    if not pending:
        return
    rows = list(pending.values())
    pending.clear()
    await upsert_candles(rows)
    for row in rows:
        await notify(
            client,
            "candle",
            SYMBOL,
            row.timeframe,
            _realtime_payload(row),
        )


async def _stream_once(
    client: httpx.AsyncClient, timeframe: str, notify: Notify
) -> None:
    pending: dict[str, CandleRow] = {}
    last_flush = time.monotonic()
    async with websockets.connect(
        KRAKEN_WS_URL,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=10,
        max_queue=1024,
    ) as socket:
        await socket.send(json.dumps(_subscription(timeframe)))
        log.info("connected symbol=%s timeframe=%s", SYMBOL, timeframe)

        while True:
            message: str | bytes | None = None
            try:
                message = await asyncio.wait_for(socket.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

            if message is not None:
                payload = json.loads(message)
                if payload.get("method") == "subscribe" and payload.get("success") is False:
                    raise RuntimeError(f"Kraken subscription rejected: {payload.get('error', payload)}")
                for row in candle_rows(payload):
                    pending[row.timeframe] = row

            now = time.monotonic()
            if pending and now - last_flush >= FLUSH_INTERVAL_S:
                await _flush(pending, client, notify)
                last_flush = now


async def _run_timeframe_stream(
    client: httpx.AsyncClient, timeframe: str, notify: Notify
) -> None:
    """Run one timeframe forever with bounded reconnect backoff."""
    backoff = 1.0
    while True:
        try:
            await _stream_once(client, timeframe, notify)
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — reconnect is the recovery path
            log.warning(
                "disconnected timeframe=%s retry_s=%.0f err=%s",
                timeframe,
                backoff,
                exc,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)


async def run_kraken_stream(
    client: httpx.AsyncClient, timeframes: list[str], notify: Notify
) -> None:
    """Run one Kraken connection per timeframe.

    Kraken accepts only one OHLC interval for a symbol on each connection, so
    isolating them also lets a failed interval reconnect without interrupting
    the others.
    """
    supported = sorted(set(timeframes) & _TIMEFRAME_TO_INTERVAL.keys())
    unsupported = sorted(set(timeframes) - _TIMEFRAME_TO_INTERVAL.keys())
    if unsupported:
        log.warning("unsupported_timeframes requested=%s", unsupported)
    if not supported:
        log.warning("no_supported_timeframes requested=%s", timeframes)
        return
    await asyncio.gather(
        *(_run_timeframe_stream(client, timeframe, notify) for timeframe in supported)
    )
