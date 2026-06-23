"""Unified strategy framework (Phase 4).

Every strategy implements the `Strategy` protocol in `base.py` and emits
`SignalCandidate`s. Candidates are POSTed to the API gate
(`POST /api/signals/candidate`), which is the single place AI validation and the
risk engine run before a signal becomes PENDING.
"""
from .base import BarWindow, IndicatorBar, RANGING, SignalCandidate, Strategy, TRENDING, VOLATILE
from .registry import STRATEGY_FACTORIES, build_strategy

__all__ = [
    "BarWindow",
    "IndicatorBar",
    "SignalCandidate",
    "Strategy",
    "TRENDING",
    "RANGING",
    "VOLATILE",
    "STRATEGY_FACTORIES",
    "build_strategy",
]
