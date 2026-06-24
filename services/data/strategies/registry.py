"""Strategy registry: maps a strategy name to its implementation class.

The runner reads the enabled strategies (and their params) from the `Strategy`
table, then instantiates each via `build_strategy`.
"""
from __future__ import annotations

from typing import Any, Callable

from .base import Strategy
from .ict import IctConfluence, IctFvg, IctOrderBlock, IctRandomBaseline, IctSweepMss
from .meanrev_rsi import MeanRevRsi
from .scalp_ema import ScalpEma
from .trend_ema import TrendEma

STRATEGY_FACTORIES: dict[str, Callable[[dict[str, Any] | None], Strategy]] = {
    "trend_ema": TrendEma,
    "meanrev_rsi": MeanRevRsi,
    "scalp_ema": ScalpEma,
    # ICT detector family (build plan §3) — multi-bar, full-OHLC price-action.
    "ict_sweep_mss": IctSweepMss,
    "ict_order_block": IctOrderBlock,
    "ict_fvg": IctFvg,
    # The aggregator: confluence of the above + premium/discount + killzone → ≤1 signal.
    "ict_confluence": IctConfluence,
    # Geometry-matched random control for ict_confluence (build plan §8). Same
    # killzone + structural stop + min-RR target frame, but random direction —
    # the significance baseline ict_confluence's OOS edge must beat.
    "ict_random_baseline": IctRandomBaseline,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    factory = STRATEGY_FACTORIES.get(name)
    if factory is None:
        raise KeyError(f"Unknown strategy '{name}'. Known: {sorted(STRATEGY_FACTORIES)}")
    return factory(params)
