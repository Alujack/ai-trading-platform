"""Broker abstraction — port of `execution/broker/types.ts`.

A `Broker` is the seam between the execution decider and wherever an order
actually lives: a local simulation (`PaperBroker`) or a real account behind the
MT5 bridge (`ExnessBroker`). The risk engine and gate still run BEFORE any
broker call; a broker can only place/close/report, never loosen a risk check.

Sizing is expressed in LOTS at this layer (already converted from the risk
engine's raw units via `lots_from_units`), because that is what MT5 trades in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

OrderSide = Literal["LONG", "SHORT"]


@dataclass(slots=True)
class BrokerAccount:
    balance: float
    equity: float
    currency: str
    marginFree: float | None = None
    leverage: float | None = None


@dataclass(slots=True)
class SymbolSpec:
    """Contract/volume metadata for one symbol, needed to size and validate orders."""

    symbol: str
    digits: int
    point: float
    #: Units of base instrument per 1.0 lot (FX major ≈ 100000; XAU ≈ 100).
    contractSize: float
    volumeMin: float
    volumeStep: float
    volumeMax: float
    #: Account-currency value of one tick for 1.0 lot — the basis for $-risk sizing.
    tickValue: float
    bid: float | None = None
    ask: float | None = None


@dataclass(slots=True)
class PlaceOrderRequest:
    #: Internal symbol (EURUSD); the broker maps it to its native name.
    symbol: str
    side: OrderSide
    #: Volume in lots, already step-clamped by the caller.
    lots: float
    stopLoss: float
    takeProfit: float
    #: Deterministic idempotency tag — our signalId. A retry must never double-fill.
    clientTag: str
    #: Max slippage in points (live only).
    deviation: int | None = None
    #: Paper-only fill price. Live brokers fill at market and IGNORE this field.
    referencePrice: float | None = None


@dataclass(slots=True)
class PlaceOrderResult:
    status: Literal["filled", "rejected"]
    ticket: str | None = None
    fillPrice: float | None = None
    reason: str | None = None


@dataclass(slots=True)
class ClosePositionResult:
    status: Literal["closed", "not_found", "error"]
    ticket: str | None = None
    exitPrice: float | None = None
    #: Realized profit in account currency.
    profit: float | None = None
    reason: str | None = None


@dataclass(slots=True)
class BrokerPosition:
    ticket: str
    #: Broker-native symbol as reported by the venue.
    symbol: str
    side: OrderSide
    lots: float
    openPrice: float
    stopLoss: float
    takeProfit: float
    #: Unrealized profit in account currency.
    profit: float
    #: Our signalId, echoed back by the bridge for reconciliation.
    clientTag: str | None = None


@dataclass(slots=True)
class PositionHistory:
    """Deal history for a position the broker closed (SL/TP hit or manual close)."""

    found: bool
    exitPrice: float | None = None
    #: Total realized profit including swap/commission.
    profit: float | None = None
    #: Unix timestamp of the close (seconds).
    closeTime: float | None = None


@dataclass(slots=True)
class HealthResult:
    ok: bool
    detail: str | None = None


@runtime_checkable
class Broker(Protocol):
    """The execution seam. `name` labels the `Trade.broker` column."""

    name: str

    async def health(self) -> HealthResult: ...
    async def get_account(self) -> BrokerAccount: ...
    async def get_symbol_spec(self, symbol: str) -> SymbolSpec: ...
    async def place_order(self, req: PlaceOrderRequest) -> PlaceOrderResult: ...
    async def close_position(
        self, ticket: str, reference_exit_price: float | None = None
    ) -> ClosePositionResult: ...
    async def get_positions(self) -> list[BrokerPosition]: ...
    async def get_position_history(self, ticket: str) -> PositionHistory | None: ...


class BrokerError(Exception):
    """Raised when the broker/bridge is unreachable or returns a non-OK status."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
