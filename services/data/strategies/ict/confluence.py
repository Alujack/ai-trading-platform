"""ict_confluence — the daily-signal aggregator (build plan §5).

This is the actual product: rather than trade each PD array on its own (which the
backtest showed is a losing proposition — and which concepts §4 predicts), it
fires ONLY when several arrays *stack* at the same price, in the bias direction,
inside premium/discount, during a killzone. It emits at most one candidate per
bar; on most bars it emits nothing, which is the intended behaviour ("a system
that takes zero trades on a bad day is working correctly").

Confluence score = weighted sum of the arrays present at the reaction zone
(weights from build plan §5; OTE included, SMT / Silver-Bullet not yet built so
their weights are simply absent). A candidate is emitted only when
``score ≥ min_score`` AND at least two arrays agree AND ``RR ≥ min_rr``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..base import TRENDING, RANGING, VOLATILE, BarWindow, Drawing, IndicatorBar, SignalCandidate
from . import primitives as P
from . import killzones as KZ
from ._base import resolve_target, signal_id

ZERO = Decimal("0")

# Weights (build plan §5). Only the arrays implemented so far appear here.
W_SWEEP = Decimal("0.30")
W_OB = Decimal("0.20")
W_FVG = Decimal("0.20")
W_OTE = Decimal("0.15")


@dataclass(slots=True)
class _Zone:
    """The assembled reaction zone for one direction, with its contributors."""

    score: Decimal
    contributors: list[str]
    entry: Decimal
    invalidation: Decimal   # the price beyond which the setup is wrong (pre-buffer)
    drawings: list[Drawing]


class IctConfluence:
    name = "ict_confluence"
    regimes = {TRENDING, RANGING, VOLATILE}
    default_lookback = 90

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.swing_k = int(p.get("swingK", 2))
        self.atr_buffer = Decimal(str(p.get("atrBuffer", "0.5")))
        self.min_rr = Decimal(str(p.get("minRr", "2.0")))
        self.min_score = Decimal(str(p.get("minScore", "0.40")))
        self.sweep_lookback = int(p.get("sweepLookback", 6))
        self.require_discount = bool(p.get("requireDiscount", True))
        self.use_bias = bool(p.get("useBias", True))
        self.use_killzone = bool(p.get("useKillzone", True))
        self.killzones = tuple(p.get("killzones", KZ.DEFAULT_KILLZONES))
        self.cooldown_ms = int(p.get("cooldownMs", 3_600_000))
        self.ai_min_score = int(p.get("aiMinScore", 75))
        self.lookback = int(p.get("lookback", self.default_lookback))

    # --------------------------------------------------------------------- #
    def evaluate(self, window: BarWindow):  # noqa: ANN201
        chrono = list(reversed(window.bars))
        if len(chrono) < (2 * self.swing_k + 3) or not P.window_has_ohlc(chrono):
            return []
        latest = chrono[-1]
        atr = latest.atr
        if atr is None or atr <= ZERO:
            return []

        # Killzone gate (intraday only; daily bars have no intraday time).
        if (
            self.use_killzone
            and KZ.timeframe_is_intraday(window.timeframe)
            and not KZ.in_killzone(latest.timestamp, self.killzones)
        ):
            return []

        swings = P.find_swings(chrono, k=self.swing_k)
        fvgs = P.find_fvgs(chrono, require_displacement=True)
        obs = P.find_order_blocks(chrono, swings)
        bias = P.ema_bias(latest)
        decision = len(chrono) - 1

        best: SignalCandidate | None = None
        best_rr = Decimal("-1")
        for direction in ("LONG", "SHORT"):
            if self.use_bias and bias is not None and bias != direction:
                continue
            zone = self._assemble(chrono, swings, fvgs, obs, decision, atr, direction)
            if zone is None or zone.score < self.min_score or len(zone.contributors) < 2:
                continue
            if direction == "LONG":
                stop = zone.invalidation - self.atr_buffer * atr
                liq = P.nearest_liquidity_above(swings, zone.entry, known_by=decision)
            else:
                stop = zone.invalidation + self.atr_buffer * atr
                liq = P.nearest_liquidity_below(swings, zone.entry, known_by=decision)
            plan = resolve_target(direction, zone.entry, stop, liq.price if liq else None, self.min_rr)
            if plan is None:
                continue
            if plan.rr > best_rr:
                best_rr = plan.rr
                best = self._build(window, latest, direction, zone, stop, plan, liq, bias)
        return [best] if best is not None else []

    # --------------------------------------------------------------------- #
    def _dealing_range(self, swings, decision):
        """Most recent confirmed swing low & high (the active range for premium/
        discount and OTE). Returns (low_swing, high_swing) or (None, None)."""
        lo = P.last_swing(swings, "low", before_index=decision + 1, known_by=decision)
        hi = P.last_swing(swings, "high", before_index=decision + 1, known_by=decision)
        return lo, hi

    def _assemble(self, chrono, swings, fvgs, obs, decision, atr, direction) -> _Zone | None:
        latest = chrono[-1]
        assert latest.low is not None and latest.high is not None and latest.close is not None
        lo_sw, hi_sw = self._dealing_range(swings, decision)

        # Premium/discount gate (concepts §3.7): longs only in discount, shorts in
        # premium, relative to the active range's equilibrium.
        if self.require_discount and lo_sw is not None and hi_sw is not None and hi_sw.price > lo_sw.price:
            eq = (lo_sw.price + hi_sw.price) / Decimal("2")
            rng = hi_sw.price - lo_sw.price
            if direction == "LONG" and latest.close > eq:
                return None
            if direction == "SHORT" and latest.close < eq:
                return None
        else:
            eq = None
            rng = None

        score = ZERO
        contributors: list[str] = []
        drawings: list[Drawing] = []
        invalidations: list[Decimal] = []
        entry_candidates: list[tuple[int, Decimal]] = []  # (priority, price) — higher priority wins

        want = direction
        # --- Sweep (highest weight) ---
        sweep = P.detect_sweep(
            chrono, swings,
            end_index=decision,
            side="SSL" if direction == "LONG" else "BSL",
            lookback=self.sweep_lookback,
        )
        if sweep is not None:
            score += W_SWEEP
            contributors.append(f"liquidity sweep {sweep.side}@{sweep.level}")
            invalidations.append(sweep.extreme)
            drawings.append(Drawing("hline", [Drawing._pt(None, sweep.level)], color="#a855f7", label=f"swept {sweep.side}"))

        # --- Order block (retested, unmitigated) ---
        for ob in reversed(obs):
            if ob.direction != want or ob.bos_index >= decision:
                continue
            if not P.ob_unmitigated_until(chrono, ob, decision):
                continue
            tapped = (latest.low <= ob.high and latest.close > ob.low) if direction == "LONG" \
                else (latest.high >= ob.low and latest.close < ob.high)
            if not tapped:
                continue
            score += W_OB
            contributors.append(f"OB {ob.low}-{ob.high}")
            invalidations.append(ob.low if direction == "LONG" else ob.high)
            entry_candidates.append((2, ob.proximal))
            drawings.append(Drawing("box", [Drawing._pt(ob.timestamp, ob.low), Drawing._pt(latest.timestamp, ob.high)], color="#f59e0b", label="OB"))
            break

        # --- FVG (tapped at CE, unmitigated) ---
        for fvg in reversed(fvgs):
            if fvg.direction != want or fvg.index >= decision:
                continue
            if not P.fvg_unmitigated_until(chrono, fvg, decision):
                continue
            tapped = (latest.low <= fvg.ce and latest.close > fvg.low) if direction == "LONG" \
                else (latest.high >= fvg.ce and latest.close < fvg.high)
            if not tapped:
                continue
            score += W_FVG
            contributors.append(f"FVG {fvg.low}-{fvg.high}")
            invalidations.append(fvg.low if direction == "LONG" else fvg.high)
            entry_candidates.append((1, fvg.ce))
            drawings.append(Drawing("box", [Drawing._pt(fvg.timestamp, fvg.low), Drawing._pt(latest.timestamp, fvg.high)], color="#3b82f6", label="FVG"))
            break

        # --- OTE (0.62–0.79 retracement of the active leg) ---
        if rng is not None and rng > ZERO and lo_sw is not None and hi_sw is not None:
            if direction == "LONG":
                ote_hi = hi_sw.price - Decimal("0.62") * rng
                ote_lo = hi_sw.price - Decimal("0.79") * rng
                in_ote = ote_lo <= latest.low <= ote_hi
            else:
                ote_lo = lo_sw.price + Decimal("0.62") * rng
                ote_hi = lo_sw.price + Decimal("0.79") * rng
                in_ote = ote_lo <= latest.high <= ote_hi
            if in_ote:
                score += W_OTE
                contributors.append("OTE 0.62-0.79")
                drawings.append(Drawing("zone", [Drawing._pt(None, ote_lo), Drawing._pt(None, ote_hi)], color="#10b981", label="OTE"))

        if not contributors or not invalidations:
            return None

        # Entry: the strongest array's reaction level (OB proximal > FVG CE), else
        # the decision-bar close (a pure sweep+OTE setup).
        entry = (
            max(entry_candidates, key=lambda x: x[0])[1] if entry_candidates else latest.close
        )
        invalidation = min(invalidations) if direction == "LONG" else max(invalidations)
        return _Zone(score=score, contributors=contributors, entry=entry, invalidation=invalidation, drawings=drawings)

    # --------------------------------------------------------------------- #
    def _build(self, window, latest, direction, zone, stop, plan, liq, bias):  # noqa: ANN001
        kz = KZ.active_killzone(latest.timestamp, self.killzones) if KZ.timeframe_is_intraday(window.timeframe) else "n/a"
        liq_txt = f"opposing liquidity {liq.price}" if plan.source == "liquidity" else f"{self.min_rr}R projection"
        reasoning = (
            f"Confluence {zone.score:.2f} ({len(zone.contributors)} arrays): "
            f"{'; '.join(zone.contributors)}. Bias {bias or 'n/a'}, killzone {kz}, "
            f"{direction.lower()} in {'discount' if direction == 'LONG' else 'premium'}. "
            f"Entry {zone.entry}, SL {stop}, TP {plan.target} via {liq_txt} → RR {plan.rr:.2f}."
        )
        drawings = list(zone.drawings) + [
            Drawing("hline", [Drawing._pt(None, stop)], color="#ef4444", label="SL"),
            Drawing("hline", [Drawing._pt(None, plan.target)], color="#22c55e", label="TP"),
            Drawing("arrow", [Drawing._pt(latest.timestamp, zone.entry)],
                    color="#22c55e" if direction == "LONG" else "#ef4444", label=f"{direction} entry"),
        ]
        confidence = max(0, min(95, 50 + int(zone.score * Decimal("50"))))
        return SignalCandidate(
            strategy_name=self.name,
            symbol=window.symbol,
            timeframe=window.timeframe,
            direction=direction,
            entry=zone.entry,
            stop=stop,
            target=plan.target,
            confidence=confidence,
            reasoning=reasoning,
            client_id=signal_id(window.symbol, window.timeframe, self.name, direction, latest.timestamp),
            cooldown_ms=self.cooldown_ms,
            ai_min_score=self.ai_min_score,
            drawings=drawings,
        )
