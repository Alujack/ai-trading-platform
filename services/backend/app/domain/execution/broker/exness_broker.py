"""ExnessBroker — port of `execution/broker/exnessBroker.ts`.

HTTP client for the MT5 bridge that runs on the Windows host beside the
MetaTrader 5 terminal. It speaks the bridge's JSON contract
(`services/mt5bridge`); it holds no MT5 logic itself.

The bridge is the only supported route to a retail Exness account (Exness has no
REST trading API). SL/TP are sent with every order so the broker manages exits
even if this service is down.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from .symbols import broker_symbol
from .types import (
    BrokerAccount,
    BrokerError,
    BrokerPosition,
    ClosePositionResult,
    HealthResult,
    PlaceOrderRequest,
    PlaceOrderResult,
    PositionHistory,
    SymbolSpec,
)

log = logging.getLogger("backend.broker")


class ExnessBroker:
    """MT5-bridge-backed broker. `env` labels it `exness_demo` / `exness_real`."""

    def __init__(
        self,
        base_url: str,
        token: str,
        env: str = "demo",
        timeout_s: float = 10.0,
        default_deviation: int = 20,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_s = timeout_s
        self._default_deviation = default_deviation
        self.name = f"exness_{env}"

    async def _call(self, path: str, method: str = "GET", body: Any | None = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                res = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    json=body,
                    headers={"content-type": "application/json", "x-bridge-token": self._token},
                )
        except Exception as exc:
            raise BrokerError(f"mt5 bridge unreachable: {exc}") from exc
        if res.status_code >= 400:
            raise BrokerError(
                f"mt5 bridge {path} -> http {res.status_code}: {res.text[:200]}",
                res.status_code,
            )
        return res.json()

    async def health(self) -> HealthResult:
        try:
            r = await self._call("/health")
            return HealthResult(ok=bool(r.get("ok")), detail=r.get("detail"))
        except BrokerError as exc:
            return HealthResult(ok=False, detail=str(exc))

    async def login(self, login: int, password: str, server: str) -> dict[str, Any]:
        """Log the bridge's MT5 terminal into an account at runtime.

        Uses creds the user set in the UI instead of the bridge's own `.env`.
        Never raises — returns a `{ok, detail}` verdict so the settings UI can
        show pass/fail. The password is never logged.
        """
        try:
            r = await self._call(
                "/session/login",
                "POST",
                {"login": login, "password": password, "server": server},
            )
            ok = bool(r.get("ok"))
            return {"ok": ok, "detail": r.get("detail") or ("logged in" if ok else "login failed")}
        except BrokerError as exc:
            return {"ok": False, "detail": str(exc)}

    async def get_account(self) -> BrokerAccount:
        r = await self._call("/account")
        return BrokerAccount(
            balance=float(r["balance"]),
            equity=float(r["equity"]),
            currency=r.get("currency", "USD"),
            marginFree=r.get("marginFree"),
            leverage=r.get("leverage"),
        )

    async def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        native = broker_symbol(symbol)
        r = await self._call(f"/symbol/{quote(native, safe='')}")
        return SymbolSpec(
            symbol=r.get("symbol", native),
            digits=int(r["digits"]),
            point=float(r["point"]),
            contractSize=float(r["contractSize"]),
            volumeMin=float(r["volumeMin"]),
            volumeStep=float(r["volumeStep"]),
            volumeMax=float(r["volumeMax"]),
            tickValue=float(r["tickValue"]),
            bid=r.get("bid"),
            ask=r.get("ask"),
        )

    async def place_order(self, req: PlaceOrderRequest) -> PlaceOrderResult:
        r = await self._call(
            "/order",
            "POST",
            {
                "symbol": broker_symbol(req.symbol),
                "side": req.side,
                "lots": req.lots,
                "sl": req.stopLoss,
                "tp": req.takeProfit,
                "clientTag": req.clientTag,
                "deviation": req.deviation if req.deviation is not None else self._default_deviation,
            },
        )
        return PlaceOrderResult(
            status=r.get("status", "rejected"),
            ticket=None if r.get("ticket") is None else str(r["ticket"]),
            fillPrice=r.get("fillPrice"),
            reason=r.get("reason"),
        )

    async def close_position(
        self, ticket: str, reference_exit_price: float | None = None
    ) -> ClosePositionResult:
        # The live broker closes at market; reference_exit_price (paper-only) is ignored.
        r = await self._call("/close", "POST", {"ticket": ticket})
        return ClosePositionResult(
            status=r.get("status", "error"),
            ticket=None if r.get("ticket") is None else str(r["ticket"]),
            exitPrice=r.get("exitPrice"),
            profit=r.get("profit"),
            reason=r.get("reason"),
        )

    async def get_positions(self) -> list[BrokerPosition]:
        r = await self._call("/positions")
        return [
            BrokerPosition(
                ticket=str(p["ticket"]),
                symbol=p["symbol"],
                side=p["side"],
                lots=float(p["lots"]),
                openPrice=float(p["openPrice"]),
                stopLoss=float(p["stopLoss"]),
                takeProfit=float(p["takeProfit"]),
                profit=float(p["profit"]),
                clientTag=p.get("clientTag"),
            )
            for p in (r.get("positions") or [])
        ]

    async def get_position_history(self, ticket: str) -> PositionHistory | None:
        try:
            r = await self._call(f"/history/{quote(ticket, safe='')}")
        except BrokerError:
            return None
        return PositionHistory(
            found=bool(r.get("found")),
            exitPrice=r.get("exitPrice"),
            profit=r.get("profit"),
            closeTime=r.get("closeTime"),
        )
