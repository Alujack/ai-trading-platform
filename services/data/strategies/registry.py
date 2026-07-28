"""Strategy registry: maps a strategy name to its implementation class.

The runner reads the enabled strategies (and their params) from the `Strategy`
table, then instantiates each via `build_strategy`.

Currently EMPTY — all previous strategies were removed (none survived
out-of-sample validation; see git history for their code and results). To add
a new strategy: implement the `Strategy` protocol from `base.py`, import it
here, and register it in `STRATEGY_FACTORIES`. Then backtest it
(`backtester.py`), walk-forward it (`walkforward.py`), and paper trade before
any live use — per CLAUDE.md.
"""
from __future__ import annotations

from typing import Any, Callable

from .base import Strategy

STRATEGY_FACTORIES: dict[str, Callable[[dict[str, Any] | None], Strategy]] = {}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    factory = STRATEGY_FACTORIES.get(name)
    if factory is None:
        raise KeyError(f"Unknown strategy '{name}'. Known: {sorted(STRATEGY_FACTORIES)}")
    return factory(params)
