"""SSE stream + internal notify — port of `routes/stream.routes.ts`.

Preserves the exact stream contract the dashboard's `EventSource` expects: an
`event: hello` frame on connect, one `data:` frame per realtime event, a `: ping`
comment heartbeat every 25s, and the no-buffering headers.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ...core.logging import get_logger
from ...core.realtime import publish_event
from ...db.redis_client import RT_CHANNEL, redis_client

log = get_logger("backend.rt")
router = APIRouter(tags=["realtime"])

HEARTBEAT_S = 25.0

SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _event_stream(request: Request) -> AsyncIterator[bytes]:
    """Yield SSE frames until the client disconnects."""
    yield b'event: hello\ndata: {"ok":true}\n\n'

    # Each client gets its own subscriber connection (a subscribed connection
    # can't issue other commands).
    pubsub = redis_client().pubsub()
    subscribed = False
    try:
        await pubsub.subscribe(RT_CHANNEL)
        subscribed = True
    except Exception as exc:  # noqa: BLE001
        log.error("[rt] subscribe failed: %s", exc)
        yield b'event: error\ndata: {"ok":false}\n\n'

    last_beat = asyncio.get_running_loop().time()
    try:
        while True:
            if await request.is_disconnected():
                break
            message = None
            if subscribed:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                except Exception as exc:  # noqa: BLE001
                    # A Redis hiccup degrades realtime, it must not kill the stream.
                    log.warning("[rt] read failed: %s", exc)
                    await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(1.0)

            if message and message.get("data"):
                payload = message["data"]
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8", "replace")
                yield f"data: {payload}\n\n".encode()

            now = asyncio.get_running_loop().time()
            if now - last_beat >= HEARTBEAT_S:
                last_beat = now
                yield b": ping\n\n"
    finally:
        if subscribed:
            try:
                await pubsub.unsubscribe(RT_CHANNEL)
            except Exception:  # noqa: BLE001
                pass
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-Sent Events: one message per realtime event, no polling lag."""
    return StreamingResponse(_event_stream(request), headers=SSE_HEADERS)


@router.post("/api/internal/rt-notify")
async def rt_notify(request: Request, response: Response) -> dict[str, Any]:
    """The Python worker pings this after writing candles/indicators.

    It can't reach Redis directly in every deployment, so we fan the event out to
    SSE clients on its behalf.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    type_ = body.get("type")
    if type_:
        await publish_event(type_, symbol=body.get("symbol"), timeframe=body.get("timeframe"))
    response.status_code = 202
    return {"ok": True}
