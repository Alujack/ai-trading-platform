"""Strategy registry: maps a strategy name to its implementation class.

The runner reads the enabled strategies (and their params) from the `Strategy`
table, then instantiates each via `build_strategy`.
"""
from __future__ import annotations

from typing import Any, Callable

from .base import Strategy
from .meanrev_rsi import MeanRevRsi
from .trend_ema import TrendEma

STRATEGY_FACTORIES: dict[str, Callable[[dict[str, Any] | None], Strategy]] = {
    "trend_ema": TrendEma,
    "meanrev_rsi": MeanRevRsi,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    factory = STRATEGY_FACTORIES.get(name)
    if factory is None:
        raise KeyError(f"Unknown strategy '{name}'. Known: {sorted(STRATEGY_FACTORIES)}")
    return factory(params)
