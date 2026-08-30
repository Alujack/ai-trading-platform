import json

import pytest

from app.core import realtime


@pytest.mark.asyncio
async def test_publish_event_includes_full_candle(monkeypatch) -> None:
    messages: list[tuple[str, str]] = []

    async def capture(channel: str, payload: str) -> None:
        messages.append((channel, payload))

    monkeypatch.setattr(realtime, "publish", capture)
    candle = {
        "symbol": "BTCUSD",
        "timeframe": "1min",
        "timestamp": "2026-08-30T03:14:00.000Z",
        "open": "78080.1",
        "high": "78090.2",
        "low": "78078.0",
        "close": "78088.4",
        "volume": "2.5",
    }

    await realtime.publish_event(
        "candle",
        symbol="BTCUSD",
        timeframe="1min",
        candle=candle,
    )

    assert len(messages) == 1
    event = json.loads(messages[0][1])
    assert event["type"] == "candle"
    assert event["symbol"] == "BTCUSD"
    assert event["timeframe"] == "1min"
    assert event["candle"] == candle
