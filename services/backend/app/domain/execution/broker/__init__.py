"""Broker factory — port of `execution/broker/index.ts`.

Selects the execution broker from settings:
  BROKER=paper   (default) → PaperBroker
  BROKER=exness            → ExnessBroker (requires MT5_BRIDGE_URL + MT5_BRIDGE_TOKEN)
  EXNESS_ENV=demo|real     → labels the live broker (demo default; real is the
                             promotion-gated flip)

Going from demo to real is ONLY this env change — no code path differs.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ....core.settings import get_settings
from .credentials import get_active_credential
from .exness_broker import ExnessBroker
from .paper_broker import PaperBroker
from .symbols import broker_symbol, lots_from_units, reset_symbol_map
from .types import (
    Broker,
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

_singleton: Broker | None = None


def get_broker() -> Broker:
    """The process-wide broker, memoized like the TypeScript factory."""
    global _singleton
    if _singleton is not None:
        return _singleton

    cfg = get_settings()
    kind = cfg.broker

    if kind == "paper":
        _singleton = PaperBroker()
        return _singleton

    if kind == "exness":
        if not cfg.mt5_bridge_url:
            raise ValueError("BROKER=exness requires MT5_BRIDGE_URL")
        if not cfg.mt5_bridge_token:
            raise ValueError("BROKER=exness requires MT5_BRIDGE_TOKEN")
        env = "real" if cfg.exness_env == "real" else "demo"
        _singleton = ExnessBroker(
            base_url=cfg.mt5_bridge_url,
            token=cfg.mt5_bridge_token,
            env=env,
            default_deviation=cfg.default_deviation,
        )
        return _singleton

    raise ValueError(f"unknown BROKER='{kind}' (expected paper|exness)")


async def ensure_broker_session(session: AsyncSession) -> dict[str, object]:
    """Push the UI-configured MT5 credentials to the bridge.

    No-op for the paper broker. Returns a `{ok, detail}` verdict (never raises)
    so the settings UI and startup can surface the outcome.
    """
    if get_settings().broker != "exness":
        return {"ok": True, "detail": "paper broker — no MT5 session needed"}

    cred = await get_active_credential(session)
    if cred is None:
        return {"ok": False, "detail": "no broker credentials configured (Settings → Broker)"}

    broker = get_broker()
    if not isinstance(broker, ExnessBroker):
        return {"ok": False, "detail": "active broker is not exness"}
    return await broker.login(cred.login, cred.password, cred.server)


def reset_broker() -> None:
    """Test-only: drop the memoized broker so env changes take effect."""
    global _singleton
    _singleton = None
    reset_symbol_map()


__all__ = [
    "Broker",
    "BrokerAccount",
    "BrokerError",
    "BrokerPosition",
    "ClosePositionResult",
    "ExnessBroker",
    "HealthResult",
    "PaperBroker",
    "PlaceOrderRequest",
    "PlaceOrderResult",
    "PositionHistory",
    "SymbolSpec",
    "broker_symbol",
    "ensure_broker_session",
    "get_broker",
    "lots_from_units",
    "reset_broker",
]
