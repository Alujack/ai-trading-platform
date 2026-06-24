"""ict_order_block — Order Block retest after a break of structure (build plan §3, row 2).

Fires when price returns to a still-live order block — the last opposite-close
candle before a displacement leg that broke structure — in the bias direction.
Entry at the proximal edge, stop beyond the far edge, target the next opposing
liquidity pool (min-RR projection otherwise). Concepts §3.3.
"""
from __future__ import annotations

from decimal import Decimal

from ..base import BarWindow, Drawing, IndicatorBar, SignalCandidate
from . import primitives as P
from ._base import IctBase, confidence_from_rr, resolve_target, signal_id


class IctOrderBlock(IctBase):
    name = "ict_order_block"
    base_confidence = 56

    def _evaluate(self, chrono: list[IndicatorBar], window: BarWindow):
        n = len(chrono)
        latest = chrono[-1]
        atr = latest.atr
        assert atr is not None
        bias = P.ema_bias(latest)
        swings = P.find_swings(chrono, k=self.swing_k)
        obs = P.find_order_blocks(chrono, swings)
        decision = n - 1

        best: SignalCandidate | None = None
        best_rr = Decimal("-1")

        for direction in ("LONG", "SHORT"):
            if bias is not None and bias != direction:
                continue
            for ob in reversed(obs):
                if ob.direction != direction or ob.bos_index >= decision:
                    continue
                if not P.ob_unmitigated_until(chrono, ob, decision):
                    continue
                assert latest.low is not None and latest.high is not None and latest.close is not None
                if direction == "LONG":
                    retested = latest.low <= ob.high and latest.close > ob.low
                    if not retested:
                        continue
                    entry = ob.proximal  # ob.high
                    stop = ob.low - self.atr_buffer * atr
                    liq = P.nearest_liquidity_above(swings, entry, known_by=decision)
                else:
                    retested = latest.high >= ob.low and latest.close < ob.high
                    if not retested:
                        continue
                    entry = ob.proximal  # ob.low
                    stop = ob.high + self.atr_buffer * atr
                    liq = P.nearest_liquidity_below(swings, entry, known_by=decision)

                plan = resolve_target(direction, entry, stop, liq.price if liq else None, self.min_rr)
                if plan is None:
                    continue
                if plan.rr > best_rr:
                    best_rr = plan.rr
                    best = self._build(window, latest, ob, direction, entry, stop, plan, liq)
                break  # newest qualifying OB per direction

        return [best] if best is not None else []

    def _build(self, window, latest, ob, direction, entry, stop, plan, liq):  # noqa: ANN001
        side = "bullish" if direction == "LONG" else "bearish"
        liq_txt = (
            f"opposing liquidity {liq.price}" if plan.source == "liquidity" else f"{self.min_rr}R projection"
        )
        reasoning = (
            f"{side.capitalize()} OB {ob.low}–{ob.high} formed {ob.timestamp.isoformat()}, "
            f"validated by a displacement break of structure; price retested it in a "
            f"{direction.lower()} EMA bias. Entry {entry} (proximal), SL beyond OB "
            f"({stop}), TP {plan.target} via {liq_txt} → RR {plan.rr:.2f}."
        )
        drawings = [
            Drawing(
                "box",
                [Drawing._pt(ob.timestamp, ob.low), Drawing._pt(latest.timestamp, ob.high)],
                color="#f59e0b",
                label=f"{side} OB",
            ),
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
