"""ICT detector family (Phase 1 of the ICT Daily Signal Engine build plan).

Each detector is an ordinary `Strategy` (see ``strategies/base.py``) so it plugs
straight into the existing live runner, backtester, and walk-forward harness —
no parallel framework. They differ from the close-only strategies in two ways:

* they need full **OHLC** and a multi-bar window, so each declares a ``lookback``;
* their logic is built from the shared, look-ahead-safe primitives in
  ``primitives.py`` (swings, displacement, FVGs, order blocks, liquidity sweeps,
  structure breaks), every one of which honours a confirmation lag.

Detectors shipped in this phase (build plan §3, table rows 1–3):
    ict_sweep_mss     — Liquidity Sweep + Market-Structure-Shift
    ict_order_block   — Order Block retest after a break of structure
    ict_fvg           — Fair Value Gap fill at consequent encroachment
"""
from __future__ import annotations

from .confluence import IctConfluence
from .fvg import IctFvg
from .order_block import IctOrderBlock
from .random_baseline import IctRandomBaseline
from .sweep_mss import IctSweepMss

__all__ = ["IctConfluence", "IctFvg", "IctOrderBlock", "IctRandomBaseline", "IctSweepMss"]
