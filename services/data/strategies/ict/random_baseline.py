"""ict_random_baseline — the geometry-matched random control for ict_confluence.

The walk-forward harness showed ``ict_confluence`` is the first thing in the
system with a positive out-of-sample edge (build-plan §8). But a positive OOS
number is not enough on its own: the strategy trades inside a specific *frame* —
only during killzones, always with a structurally-placed stop, always targeting
≥ ``min_rr``. Some of its apparent edge could come from that frame rather than
from the ICT confluence *selection* (sweep + OB + FVG + OTE picking the
direction and the moment). A random-walk entry with a 1:R≥2 target has an
expectancy near zero before costs, so if random entries *inside the same frame*
make money too, the "edge" was just the geometry.

This strategy IS that control. It mirrors ict_confluence's geometry exactly —

  * same killzone gate (intraday only),
  * same structurally-placed stop (nearest confirmed swing ± ``atr_buffer``·ATR),
  * same target construction (``resolve_target``: next opposing liquidity pool, or
    a ``min_rr`` projection fallback),
  * same single-position / cooldown behaviour (enforced by the engine),

— but **randomises the two things confluence decides**: whether to take a trade
and which direction. Everything that makes it "ICT" (the confluence score, the
EMA bias, the premium/discount filter) is deliberately absent. What remains is
pure frame. Comparing ict_confluence's OOS expectancy against a Monte-Carlo of
this baseline (many seeds) is the significance test: confluence has a real edge
only if it lands in the right tail of the random distribution.

Determinism: the random direction at a bar is seeded from ``(seed, timestamp)``
via SHA-1 (not Python's salted ``hash``), so a given ``seed`` assigns the same
direction to the same calendar bar in every process — reproducible Monte-Carlo
runs, and independent of how many bars happened to be flat before it.
"""
from __future__ import annotations

import hashlib
import random
from decimal import Decimal
from typing import Any

from ..base import TRENDING, RANGING, VOLATILE, BarWindow, SignalCandidate
from . import primitives as P
from . import killzones as KZ
from ._base import resolve_target, signal_id

ZERO = Decimal("0")


def _coin(seed: int, ts_iso: str) -> random.Random:
    """A per-(seed, bar) RNG, reproducible across processes (PYTHONHASHSEED-proof)."""
    digest = hashlib.sha1(f"{seed}|{ts_iso}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


class IctRandomBaseline:
    name = "ict_random_baseline"
    regimes = {TRENDING, RANGING, VOLATILE}  # match confluence: never regime-gated
    default_lookback = 90

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.swing_k = int(p.get("swingK", 2))
        self.atr_buffer = Decimal(str(p.get("atrBuffer", "0.5")))
        self.min_rr = Decimal(str(p.get("minRr", "2.0")))
        self.use_killzone = bool(p.get("useKillzone", True))
        self.killzones = tuple(p.get("killzones", KZ.DEFAULT_KILLZONES))
        self.cooldown_ms = int(p.get("cooldownMs", 3_600_000))
        self.ai_min_score = int(p.get("aiMinScore", 75))
        self.lookback = int(p.get("lookback", self.default_lookback))
        self.seed = int(p.get("seed", 0))
        # Probability of taking a trade on an otherwise-valid bar. 1.0 (default)
        # fires on every valid in-killzone bar; the engine's single-position +
        # cooldown gating then spaces trades out exactly as it does for confluence.
        self.fire_prob = float(p.get("fireProb", 1.0))

    # --------------------------------------------------------------------- #
    def evaluate(self, window: BarWindow):  # noqa: ANN201
        chrono = list(reversed(window.bars))
        if len(chrono) < (2 * self.swing_k + 3) or not P.window_has_ohlc(chrono):
            return []
        latest = chrono[-1]
        atr = latest.atr
        if atr is None or atr <= ZERO:
            return []

        # Killzone gate — identical to ict_confluence (intraday only).
        if (
            self.use_killzone
            and KZ.timeframe_is_intraday(window.timeframe)
            and not KZ.in_killzone(latest.timestamp, self.killzones)
        ):
            return []

        rng = _coin(self.seed, latest.timestamp.isoformat())
        if self.fire_prob < 1.0 and rng.random() >= self.fire_prob:
            return []

        # The one thing under test: direction is a coin flip, NOT a confluence read.
        direction = "LONG" if rng.random() < 0.5 else "SHORT"

        swings = P.find_swings(chrono, k=self.swing_k)
        decision = len(chrono) - 1
        entry = latest.close
        assert entry is not None

        # Structurally-placed stop: just beyond the nearest confirmed swing on the
        # invalidation side, with the same ATR buffer confluence uses. Target via
        # the same resolve_target (opposing liquidity, else min-RR projection).
        if direction == "LONG":
            inval = P.nearest_liquidity_below(swings, entry, known_by=decision)
            if inval is None:
                return []
            stop = inval.price - self.atr_buffer * atr
            liq = P.nearest_liquidity_above(swings, entry, known_by=decision)
        else:
            inval = P.nearest_liquidity_above(swings, entry, known_by=decision)
            if inval is None:
                return []
            stop = inval.price + self.atr_buffer * atr
            liq = P.nearest_liquidity_below(swings, entry, known_by=decision)

        plan = resolve_target(direction, entry, stop, liq.price if liq else None, self.min_rr)
        if plan is None:
            return []

        kz = (
            KZ.active_killzone(latest.timestamp, self.killzones)
            if KZ.timeframe_is_intraday(window.timeframe)
            else "n/a"
        )
        reasoning = (
            f"RANDOM baseline (seed {self.seed}): {direction} coin-flip in killzone {kz}. "
            f"Structural stop {stop} (swing {inval.price} ± {self.atr_buffer}·ATR), "
            f"TP {plan.target} via {plan.source} → RR {plan.rr:.2f}. "
            f"Control for ict_confluence geometry — NOT a tradeable signal."
        )
        return [
            SignalCandidate(
                strategy_name=self.name,
                symbol=window.symbol,
                timeframe=window.timeframe,
                direction=direction,
                entry=entry,
                stop=stop,
                target=plan.target,
                confidence=50,
                reasoning=reasoning,
                client_id=signal_id(window.symbol, window.timeframe, self.name, direction, latest.timestamp),
                cooldown_ms=self.cooldown_ms,
                ai_min_score=self.ai_min_score,
            )
        ]
