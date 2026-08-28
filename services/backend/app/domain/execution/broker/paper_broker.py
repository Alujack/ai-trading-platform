"""PaperBroker — port of `execution/broker/paperBroker.ts`.

An in-process simulation conforming to the `Broker` protocol, so the same
broker-routed execution path works for paper and live. Dependency-free and
deterministic: opens fill at `referencePrice` and closes at
`reference_exit_price`, so it is fully unit-testable without a price feed or DB.
"""
from __future__ import annotations

import math

from ....core.settings import get_settings
from .symbols import broker_symbol
from .types import (
    BrokerAccount,
    BrokerPosition,
    ClosePositionResult,
    HealthResult,
    PlaceOrderRequest,
    PlaceOrderResult,
    PositionHistory,
    SymbolSpec,
)

_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def paper_spec(symbol: str) -> SymbolSpec:
    """Generic 1:1 spec — paper "lots" equal units, preserving unit-based math."""
    return SymbolSpec(
        symbol=broker_symbol(symbol),
        digits=5,
        point=1e-5,
        contractSize=1,
        volumeMin=0,
        volumeStep=1e-8,
        volumeMax=_MAX_SAFE_INTEGER,
        tickValue=1,
    )


class PaperBroker:
    """Deterministic simulator satisfying the `Broker` protocol."""

    def __init__(self, balance: float | None = None, currency: str = "USD") -> None:
        self.name = "paper"
        self._balance = (
            balance if balance is not None else get_settings().paper_account_balance
        )
        self._currency = currency
        self._positions: dict[str, BrokerPosition] = {}
        self._by_tag: dict[str, str] = {}
        self._seq = 0

    async def health(self) -> HealthResult:
        return HealthResult(ok=True, detail="paper simulator")

    async def get_account(self) -> BrokerAccount:
        open_profit = sum(p.profit for p in self._positions.values())
        return BrokerAccount(
            balance=self._balance,
            equity=self._balance + open_profit,
            currency=self._currency,
        )

    async def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        return paper_spec(symbol)

    async def place_order(self, req: PlaceOrderRequest) -> PlaceOrderResult:
        # Idempotency: a repeated clientTag returns the original fill, never a 2nd position.
        existing = self._by_tag.get(req.clientTag)
        if existing:
            pos = self._positions.get(existing)
            return PlaceOrderResult(
                status="filled", ticket=existing, fillPrice=pos.openPrice if pos else None
            )
        if req.referencePrice is None or not math.isfinite(req.referencePrice):
            return PlaceOrderResult(status="rejected", reason="paper_no_reference_price")
        if not req.lots > 0:
            return PlaceOrderResult(status="rejected", reason="non_positive_lots")
        self._seq += 1
        ticket = f"paper-{self._seq}"
        fill_price = req.referencePrice
        self._positions[ticket] = BrokerPosition(
            ticket=ticket,
            symbol=broker_symbol(req.symbol),
            side=req.side,
            lots=req.lots,
            openPrice=fill_price,
            stopLoss=req.stopLoss,
            takeProfit=req.takeProfit,
            profit=0.0,
            clientTag=req.clientTag,
        )
        self._by_tag[req.clientTag] = ticket
        return PlaceOrderResult(status="filled", ticket=ticket, fillPrice=fill_price)

    async def close_position(
        self, ticket: str, reference_exit_price: float | None = None
    ) -> ClosePositionResult:
        pos = self._positions.get(ticket)
        if pos is None:
            return ClosePositionResult(status="not_found", ticket=ticket, reason="unknown_ticket")
        exit_price = reference_exit_price if reference_exit_price is not None else pos.openPrice
        sign = 1 if pos.side == "LONG" else -1
        # contractSize folded into lots==units (paper_spec), so profit = Δprice * lots * sign.
        profit = (exit_price - pos.openPrice) * pos.lots * sign
        del self._positions[ticket]
        if pos.clientTag:
            self._by_tag.pop(pos.clientTag, None)
        return ClosePositionResult(
            status="closed", ticket=ticket, exitPrice=exit_price, profit=profit
        )

    async def get_positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    async def get_position_history(self, ticket: str) -> PositionHistory | None:
        # The simulator has no deal history; the reconciler treats None as "unknown".
        return None
