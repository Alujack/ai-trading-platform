"""Strategy allocator — dynamic weighting of the Gold strategy orchestra.

The allocator decides how to distribute risk budget across the 4 Gold
strategies based on:

  1. **Current regime** — each strategy declares its preferred regimes; the
     allocator boosts strategies whose regime matches the live classification.
  2. **Recent performance** — strategies on a hot streak get a modest size
     boost; cold-streak strategies get reduced.
  3. **Session context** — strategies are weighted by their session relevance.

The allocator does NOT replace the regime gate (``regime.py``). The regime gate
is a hard filter (blocked strategies never fire); the allocator is a *soft
weighting* that adjusts position size for approved strategies.

Usage::

    from strategy_allocator import StrategyAllocator, StrategyPerformance

    perf = {
        "gold_london_sweep": StrategyPerformance(wins=8, losses=2, streak=3),
        ...
    }
    allocator = StrategyAllocator()
    weights = allocator.compute_weights(
        regime="TRENDING", session="london", performances=perf
    )
    # weights = {"gold_london_sweep": 1.4, "gold_vwap_scalp": 1.0, ...}
    # Apply: actual_risk_pct = base_risk_pct * weight
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# The Gold strategy names this allocator manages.
GOLD_STRATEGIES = (
    "gold_london_sweep",
    "gold_asian_breakout",
    "gold_vwap_scalp",
    "gold_news_fade",
)

# Which sessions each strategy is designed for.
STRATEGY_SESSION_MAP: dict[str, tuple[str, ...]] = {
    "gold_london_sweep":   ("london",),
    "gold_asian_breakout": ("london", "ny_am"),
    "gold_vwap_scalp":     ("ny_am",),        # overlap window
    "gold_news_fade":      ("london", "ny_am"),  # news can happen any active session
}

# Which regime each strategy prefers (for soft weighting, not hard gating).
STRATEGY_REGIME_MAP: dict[str, tuple[str, ...]] = {
    "gold_london_sweep":   ("TRENDING", "RANGING"),
    "gold_asian_breakout": ("TRENDING",),
    "gold_vwap_scalp":     ("TRENDING",),
    "gold_news_fade":      ("VOLATILE",),
}


@dataclass(slots=True)
class StrategyPerformance:
    """Rolling performance window for a single strategy."""

    wins: int = 0
    losses: int = 0
    streak: int = 0          # positive = win streak, negative = loss streak
    last_20_pnl: float = 0.0  # sum of R-multiples over last 20 trades

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.5

    @property
    def trade_count(self) -> int:
        return self.wins + self.losses


@dataclass(slots=True)
class StrategyWeight:
    """Computed weight for a strategy, with breakdown of contributing factors."""

    strategy: str
    weight: float              # final multiplier (0.5 = half size, 1.5 = 50% boost)
    regime_factor: float       # 1.0 = neutral, 1.2 = regime match
    session_factor: float      # 1.0 = neutral, 1.2 = session match
    performance_factor: float  # 0.5–1.2 based on streak/win-rate
    reason: str


class StrategyAllocator:
    """Computes position-size multipliers for the Gold strategy orchestra."""

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # Hot-streak bonus: strategies on 3+ win streak.
        self.hot_streak_threshold = int(p.get("hotStreakThreshold", 3))
        self.hot_streak_bonus = float(p.get("hotStreakBonus", 0.2))  # +20%
        # Cold-streak penalty: strategies on 3+ loss streak.
        self.cold_streak_threshold = int(p.get("coldStreakThreshold", 3))
        self.cold_streak_penalty = float(p.get("coldStreakPenalty", 0.5))  # -50%
        # Regime match bonus.
        self.regime_match_bonus = float(p.get("regimeMatchBonus", 0.2))  # +20%
        # Session match bonus.
        self.session_match_bonus = float(p.get("sessionMatchBonus", 0.15))  # +15%
        # Floor and ceiling for weights.
        self.min_weight = float(p.get("minWeight", 0.3))
        self.max_weight = float(p.get("maxWeight", 1.5))

    def _performance_factor(self, perf: StrategyPerformance | None) -> float:
        """Compute the performance-based size adjustment."""
        if perf is None or perf.trade_count < 5:
            return 1.0  # not enough data to adjust

        factor = 1.0

        # Streak adjustment.
        if perf.streak >= self.hot_streak_threshold:
            factor += self.hot_streak_bonus
        elif perf.streak <= -self.cold_streak_threshold:
            factor -= self.cold_streak_penalty

        # Win rate adjustment (subtle — don't want to over-weight past performance).
        if perf.trade_count >= 10:
            if perf.win_rate > 0.65:
                factor += 0.1
            elif perf.win_rate < 0.40:
                factor -= 0.15

        return max(self.min_weight, min(self.max_weight, factor))

    def _regime_factor(self, strategy: str, regime: str) -> float:
        preferred = STRATEGY_REGIME_MAP.get(strategy, ())
        if regime in preferred:
            return 1.0 + self.regime_match_bonus
        return 1.0

    def _session_factor(self, strategy: str, session: str) -> float:
        preferred = STRATEGY_SESSION_MAP.get(strategy, ())
        if session in preferred:
            return 1.0 + self.session_match_bonus
        return 1.0

    def compute_weights(
        self,
        regime: str,
        session: str,
        performances: dict[str, StrategyPerformance] | None = None,
    ) -> dict[str, StrategyWeight]:
        """Compute position-size multipliers for all Gold strategies.

        Args:
            regime: Current market regime (TRENDING/RANGING/VOLATILE/UNKNOWN).
            session: Current session name (london/ny_am/asian/etc.).
            performances: Rolling performance data per strategy.

        Returns:
            Dict mapping strategy name → StrategyWeight with the final multiplier.
        """
        perfs = performances or {}
        weights: dict[str, StrategyWeight] = {}

        for strat in GOLD_STRATEGIES:
            r_factor = self._regime_factor(strat, regime)
            s_factor = self._session_factor(strat, session)
            p_factor = self._performance_factor(perfs.get(strat))

            # Combine multiplicatively, then clamp.
            raw = r_factor * s_factor * p_factor
            final = max(self.min_weight, min(self.max_weight, raw))

            perf = perfs.get(strat)
            perf_str = (
                f"W{perf.wins}/L{perf.losses} streak={perf.streak}" if perf else "no data"
            )
            reason = (
                f"regime={regime}→{r_factor:.2f}, session={session}→{s_factor:.2f}, "
                f"perf({perf_str})→{p_factor:.2f} → final={final:.2f}"
            )

            weights[strat] = StrategyWeight(
                strategy=strat,
                weight=final,
                regime_factor=r_factor,
                session_factor=s_factor,
                performance_factor=p_factor,
                reason=reason,
            )

        return weights

    def get_risk_multiplier(
        self,
        strategy_name: str,
        regime: str,
        session: str,
        performances: dict[str, StrategyPerformance] | None = None,
    ) -> float:
        """Convenience: get the final risk multiplier for a single strategy."""
        weights = self.compute_weights(regime, session, performances)
        sw = weights.get(strategy_name)
        return sw.weight if sw else 1.0
