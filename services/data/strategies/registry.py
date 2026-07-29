"""Strategy registry: maps a strategy name to its implementation class.

The runner reads the enabled strategies (and their params) from the `Strategy`
table, then instantiates each via `build_strategy`.

The 2026-07-28 purge removed every strategy that failed out-of-sample
validation (see git history for their code and results). Only the ICT family
is registered: it was restored from `45f5a41` because its EURUSD 60min
walk-forward was promising-but-undersized (39 OOS trades) and it was never
tested on XAUUSD — that experiment is the reason it's back. To add a new
strategy: implement the `Strategy` protocol from `base.py`, import it here,
and register it in `STRATEGY_FACTORIES`. Then backtest it (`backtester.py`),
walk-forward it (`walkforward.py`), and paper trade before any live use —
per CLAUDE.md.
"""
from __future__ import annotations

from typing import Any, Callable

from .base import Strategy
from .ict import IctConfluence, IctFvg, IctOrderBlock, IctRandomBaseline, IctSweepMss
from .ml_xau import MlXau, MlXauRandomBaseline

STRATEGY_FACTORIES: dict[str, Callable[[dict[str, Any] | None], Strategy]] = {
    # ICT detector family (docs/research/ict-signal-engine-build-plan.md §3) —
    # multi-bar, full-OHLC price-action.
    "ict_sweep_mss": IctSweepMss,
    "ict_order_block": IctOrderBlock,
    "ict_fvg": IctFvg,
    # The aggregator: confluence of the above + premium/discount + killzone → ≤1 signal.
    "ict_confluence": IctConfluence,
    # Geometry-matched random control for ict_confluence (build plan §8). Same
    # killzone + structural stop + min-RR target frame, but random direction —
    # the significance baseline ict_confluence's OOS edge must beat.
    "ict_random_baseline": IctRandomBaseline,
    # Ported LightGBM/ONNX classifier from the external `xaubot` project. 1min
    # only (trained on M1). UNVALIDATED on this platform — see ml_xau.py. Must
    # clear backtest + walkforward + random baseline before it is ever enabled.
    "ml_xau": MlXau,
    # Timing- and geometry-matched random control for ml_xau (see baseline_mc.py).
    "ml_xau_random": MlXauRandomBaseline,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    factory = STRATEGY_FACTORIES.get(name)
    if factory is None:
        raise KeyError(f"Unknown strategy '{name}'. Known: {sorted(STRATEGY_FACTORIES)}")
    return factory(params)
