"""Strategy registry: maps a strategy name to its implementation class.

The runner reads the enabled strategies (and their params) from the `Strategy`
table, then instantiates each via `build_strategy`.
"""
from __future__ import annotations

from typing import Any, Callable

from .base import Strategy
from .gold_asian_breakout import GoldAsianBreakout
from .gold_london_sweep import GoldLondonSweep
from .gold_news_fade import GoldNewsFade
from .gold_vwap_scalp import GoldVwapScalp
from .gold_zigzag_reversal import GoldZigzagReversal, GoldZigzagReversalDaily, GoldZigzagScalp
from .ict import IctConfluence, IctFvg, IctOrderBlock, IctRandomBaseline, IctSweepMss
from .meanrev_rsi import MeanRevRsi
from .scalp_ema import ScalpEma
from .scalp_sniper import ScalpSniper, ScalpSniperRandom
from .scalp_vwap import ScalpVwap
from .trend_ema import TrendEma

STRATEGY_FACTORIES: dict[str, Callable[[dict[str, Any] | None], Strategy]] = {
    "trend_ema": TrendEma,
    "meanrev_rsi": MeanRevRsi,
    "scalp_ema": ScalpEma,
    # VWAP-anchored momentum scalp — the aggressive-scalper skill's entry brain,
    # regime-gated to TRENDING only (skips chop/news). See scalp_vwap.py.
    "scalp_vwap": ScalpVwap,
    # Momentum-burst continuation (the skill's stacking entry): 3-bar burst +
    # range expansion + wick/blow-off/extension vetos + VWAP slope agreement.
    "scalp_sniper": ScalpSniper,
    # Geometry-matched random control for scalp_sniper — same session/regime/stop
    # frame, coin-flip direction. Used by baseline_mc.py, never traded live.
    "scalp_sniper_random": ScalpSniperRandom,
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
    # --- Gold strategy orchestra (XAU/USD multi-session bot) ---
    # Session-based liquidity sweep at London open; sweeps Asian H/L then reverses.
    "gold_london_sweep": GoldLondonSweep,
    # Breakout from tight Asian consolidation at London/NY open.
    "gold_asian_breakout": GoldAsianBreakout,
    # VWAP + BB bounce scalper during London-NY overlap (peak liquidity).
    "gold_vwap_scalp": GoldVwapScalp,
    # Post-spike mean reversion after high-impact news; the only VOLATILE strategy.
    "gold_news_fade": GoldNewsFade,
    # Swing-pivot (ZigZag) reversal — the legit core of the "Happy Gold" EA,
    # rebuilt without the hidden martingale/grid. One position, hard ATR stop.
    "gold_zigzag_reversal": GoldZigzagReversal,
    # Higher-frequency ZigZag tuning: ~1.2 trades/day on XAUUSD 15min (thinner
    # per-trade edge). For when daily activity matters more than max expectancy.
    "gold_zigzag_reversal_daily": GoldZigzagReversalDaily,
    # Aggressive high-frequency scalp tuning of the ZigZag engine: fixed tight
    # ATR take-profit, shallow pivots, all regimes. Max frequency, thinnest edge
    # — measure it honestly with the backtester before trusting it.
    "gold_zigzag_scalp": GoldZigzagScalp,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    factory = STRATEGY_FACTORIES.get(name)
    if factory is None:
        raise KeyError(f"Unknown strategy '{name}'. Known: {sorted(STRATEGY_FACTORIES)}")
    return factory(params)
