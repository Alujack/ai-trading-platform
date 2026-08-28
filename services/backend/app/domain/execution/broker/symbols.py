"""Symbol mapping + units→lots conversion — port of `execution/broker/symbols.ts`.

Exness symbol names are mostly unsuffixed (EURUSD, XAUUSD, BTCUSD) but vary by
account type — ALWAYS verify against the live MT5 Market Watch. Override the map
without a code change via `BROKER_SYMBOL_MAP` (JSON), e.g.
`BROKER_SYMBOL_MAP={"EURUSD":"EURUSDm","BTCUSD":"BTCUSDm"}`.
"""
from __future__ import annotations

import json
import logging

from ....core.settings import get_settings
from .types import SymbolSpec

log = logging.getLogger("backend.broker")

DEFAULT_MAP: dict[str, str] = {
    "EURUSD": "EURUSD",
    "XAUUSD": "XAUUSD",
    "BTCUSD": "BTCUSD",
}

_cached: dict[str, str] | None = None


def _load_map() -> dict[str, str]:
    global _cached
    if _cached is not None:
        return _cached
    raw = get_settings().broker_symbol_map
    if not raw or not raw.strip():
        _cached = dict(DEFAULT_MAP)
        return _cached
    try:
        override = json.loads(raw)
        _cached = {**DEFAULT_MAP, **override}
    except (json.JSONDecodeError, TypeError):
        log.error("[broker] invalid BROKER_SYMBOL_MAP JSON — using defaults")
        _cached = dict(DEFAULT_MAP)
    return _cached


def broker_symbol(internal: str) -> str:
    """Map an internal symbol (EURUSD) to the broker-native name. Identity if unmapped."""
    return _load_map().get(internal, internal)


def reset_symbol_map() -> None:
    """Test-only: clear the memoized `BROKER_SYMBOL_MAP`."""
    global _cached
    _cached = None


def lots_from_units(units: float, spec: SymbolSpec) -> float:
    """Convert a raw unit size (risk-engine output) into a broker-valid lot size.

    Divide by contract size, floor to the volume step, clamp to [min, max].
    Returns 0 when the result is below the broker's minimum (caller must reject).
    """
    import math

    if not math.isfinite(units) or units <= 0:
        return 0.0
    if not (spec.contractSize > 0) or not (spec.volumeStep > 0):
        raise ValueError("symbol spec must have positive contractSize and volumeStep")
    raw_lots = units / spec.contractSize
    steps = math.floor(raw_lots / spec.volumeStep + 1e-9)
    lots = round(steps * spec.volumeStep, 8)
    if lots < spec.volumeMin:
        return 0.0
    return min(lots, spec.volumeMax)
