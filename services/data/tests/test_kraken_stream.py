from decimal import Decimal

from kraken_stream import _realtime_payload, candle_rows


def test_ohlc_update_maps_to_platform_candle() -> None:
    rows = candle_rows(
        {
            "channel": "ohlc",
            "type": "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "interval": 60,
                    "interval_begin": "2026-08-30T03:00:00.000000Z",
                    "open": 78000.1,
                    "high": 78250.2,
                    "low": 77950.0,
                    "close": 78120.4,
                    "volume": 12.5,
                }
            ],
        }
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "BTCUSD"
    assert row.timeframe == "60min"
    assert row.timestamp.isoformat() == "2026-08-30T03:00:00"
    assert row.open == Decimal("78000.1")
    assert row.close == Decimal("78120.4")
    assert _realtime_payload(row) == {
        "symbol": "BTCUSD",
        "timeframe": "60min",
        "timestamp": "2026-08-30T03:00:00.000Z",
        "open": "78000.1",
        "high": "78250.2",
        "low": "77950.0",
        "close": "78120.4",
        "volume": "12.5",
    }


def test_non_ohlc_messages_are_ignored() -> None:
    assert candle_rows({"channel": "heartbeat", "type": "update"}) == []
    assert candle_rows({"method": "subscribe", "success": True}) == []
