"""PaperBroker parity, translated from `paperBroker.test.ts`."""
from __future__ import annotations

import pytest

from app.domain.execution.broker.paper_broker import PaperBroker
from app.domain.execution.broker.types import PlaceOrderRequest


def order(**over) -> PlaceOrderRequest:
    base = {
        "symbol": "EURUSD",
        "side": "LONG",
        "lots": 10_000,
        "stopLoss": 1.0980,
        "takeProfit": 1.1060,
        "clientTag": "sig-1",
        "referencePrice": 1.1000,
    }
    base.update(over)
    return PlaceOrderRequest(**base)  # type: ignore[arg-type]


async def test_fills_at_the_reference_price_and_reports_the_position():
    broker = PaperBroker(balance=10_000)
    result = await broker.place_order(order())
    assert result.status == "filled"
    assert result.fillPrice == 1.1000
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].clientTag == "sig-1"


async def test_is_idempotent_on_client_tag():
    broker = PaperBroker()
    first = await broker.place_order(order())
    retry = await broker.place_order(order())
    assert retry.status == "filled"
    assert retry.ticket == first.ticket
    assert len(await broker.get_positions()) == 1


async def test_rejects_without_a_reference_price():
    broker = PaperBroker()
    result = await broker.place_order(order(referencePrice=None))
    assert result.status == "rejected"
    assert result.reason == "paper_no_reference_price"


async def test_rejects_non_positive_lots():
    broker = PaperBroker()
    result = await broker.place_order(order(lots=0))
    assert result.status == "rejected"
    assert result.reason == "non_positive_lots"


async def test_computes_long_profit_on_close():
    broker = PaperBroker()
    opened = await broker.place_order(order(lots=10_000, referencePrice=1.1000))
    assert opened.ticket is not None
    closed = await broker.close_position(opened.ticket, reference_exit_price=1.1050)
    assert closed.status == "closed"
    # (1.1050 - 1.1000) * 10_000 = 50
    assert closed.profit == pytest.approx(50, abs=1e-6)
    assert await broker.get_positions() == []


async def test_computes_short_profit_on_close():
    broker = PaperBroker()
    opened = await broker.place_order(order(side="SHORT", lots=10_000, referencePrice=1.1000))
    assert opened.ticket is not None
    closed = await broker.close_position(opened.ticket, reference_exit_price=1.0950)
    # SHORT: (1.0950 - 1.1000) * 10_000 * -1 = 50
    assert closed.profit == pytest.approx(50, abs=1e-6)


async def test_returns_not_found_closing_an_unknown_ticket():
    broker = PaperBroker()
    result = await broker.close_position("nope")
    assert result.status == "not_found"


async def test_reports_account_equity_including_open_profit():
    broker = PaperBroker(balance=10_000)
    account = await broker.get_account()
    assert account.balance == 10_000
    assert account.equity == 10_000  # no marks applied → equity == balance


async def test_health_is_always_ok_for_the_simulator():
    assert (await PaperBroker().health()).ok is True
