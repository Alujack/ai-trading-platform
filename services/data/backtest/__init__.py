"""Backtesting harness for the strategy modules.

Replays stored OHLCV candles + indicators through the *unchanged* strategy
`evaluate()` implementations and simulates each resulting trade bar-by-bar with a
realistic fill/cost model, so every strategy can be assigned an expectancy, win
rate, profit factor, and max drawdown before it is allowed near paper or live
trading (CLAUDE.md: "Backtest every strategy before live use").

The engine (`engine.py`) is pure — no DB, no network — so it is unit-testable
with synthetic bars. `loader.py` pulls real history from TimescaleDB and
`report.py`/`metrics.py` turn raw trades into the headline numbers.
"""
from .engine import (
    Bar,
    BacktestConfig,
    CostModel,
    RunResult,
    Trade,
    simulate,
)
from .metrics import Metrics, summarize

__all__ = [
    "Bar",
    "BacktestConfig",
    "CostModel",
    "RunResult",
    "Trade",
    "simulate",
    "Metrics",
    "summarize",
]
