"""ict_fvg — Fair Value Gap fill (build plan §3, row 3).

Fires when price retraces into an unfilled, displacement-born FVG that is aligned
with the EMA bias, tapping the consequent encroachment (50%) level. Entry at the
CE, stop beyond the far edge of the gap, target the next opposing liquidity pool
(min-RR projection if that pool is too close). Concepts §3.5 / §3.7.
"""
from __future__ import annotations

from decimal import Decimal

from ..base import BarWindow, Drawing, IndicatorBar, SignalCandidate
from . import primitives as P
from ._base import IctBase, confidence_from_rr, resolve_target, signal_id


class IctFvg(IctBase):
    name = "ict_fvg"
    base_confidence = 52

    def _evaluate(self, chrono: list[IndicatorBar], window: BarWindow):
        n = len(chrono)
        latest = chrono[-1]
        atr = latest.atr
        assert atr is not None
        bias = P.ema_bias(latest)
        swings = P.find_swings(chrono, k=self.swing_k)
        fvgs = P.find_fvgs(chrono, require_displacement=True)
        decision = n - 1

        best: SignalCandidate | None = None
        best_rr = Decimal("-1")

        for direction in ("LONG", "SHORT"):
            if bias is not None and bias != direction:
                continue
            want = "LONG" if direction == "LONG" else "SHORT"
            # newest qualifying gap first
            for fvg in reversed(fvgs):
                if fvg.direction != want or fvg.index >= decision:
                    continue
                if not P.fvg_unmitigated_until(chrono, fvg, decision):
                    continue
                assert latest.low is not None and latest.high is not None and latest.close is not None
                if direction == "LONG":
                    tapped = latest.low <= fvg.ce and latest.close > fvg.low
                    if not tapped:
                        continue
                    entry = fvg.ce
                    stop = fvg.low - self.atr_buffer * atr
                    liq = P.nearest_liquidity_above(swings, entry, known_by=decision)
                else:
                    tapped = latest.high >= fvg.ce and latest.close < fvg.high
                    if not tapped:
                        continue
                    entry = fvg.ce
                    stop = fvg.high + self.atr_buffer * atr
                    liq = P.nearest_liquidity_below(swings, entry, known_by=decision)

                plan = resolve_target(direction, entry, stop, liq.price if liq else None, self.min_rr)
                if plan is None:
                    continue
                if plan.rr > best_rr:
                    best_rr = plan.rr
                    best = self._build(window, latest, fvg, direction, entry, stop, plan, liq)
                break  # only the newest qualifying gap per direction

        return [best] if best is not None else []

    def _build(self, window, latest, fvg, direction, entry, stop, plan, liq):  # noqa: ANN001
        side = "bullish" if direction == "LONG" else "bearish"
        liq_txt = (
            f"opposing liquidity {liq.price}" if plan.source == "liquidity" else f"{self.min_rr}R projection"
        )
        reasoning = (
            f"{side.capitalize()} FVG {fvg.low}–{fvg.high} (CE {fvg.ce}) formed "
            f"{fvg.timestamp.isoformat()}; price retraced into the gap at CE with "
            f"EMA bias {direction.lower()}. Entry CE {entry}, SL beyond gap "
            f"({stop}), TP {plan.target} via {liq_txt} → RR {plan.rr:.2f}."
        )
        drawings = [
            Drawing(
                "box",
                [Drawing._pt(fvg.timestamp, fvg.low), Drawing._pt(latest.timestamp, fvg.high)],
                color="#3b82f6",
                label=f"{side} FVG",
            ),
            Drawing("hline", [Drawing._pt(None, fvg.ce)], color="#94a3b8", label="CE 50%"),
            Drawing("hline", [Drawing._pt(None, stop)], color="#ef4444", label="SL"),
            Drawing("hline", [Drawing._pt(None, plan.target)], color="#22c55e", label="TP"),
            Drawing(
                "arrow",
                [Drawing._pt(latest.timestamp, entry)],
                color="#22c55e" if direction == "LONG" else "#ef4444",
                label=f"{direction} entry",
            ),
        ]
        return SignalCandidate(
            strategy_name=self.name,
            symbol=window.symbol,
            timeframe=window.timeframe,
            direction=direction,
            entry=entry,
            stop=stop,
            target=plan.target,
            confidence=confidence_from_rr(plan.rr, self.base_confidence, self.min_rr),
            reasoning=reasoning,
            client_id=signal_id(window.symbol, window.timeframe, self.name, direction, latest.timestamp),
            cooldown_ms=self.cooldown_ms,
            ai_min_score=self.ai_min_score,
            drawings=drawings,
        )
