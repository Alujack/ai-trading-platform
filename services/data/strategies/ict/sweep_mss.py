"""ict_sweep_mss — Liquidity Sweep + Market-Structure-Shift (build plan §3, row 1).

The canonical ICT reversal: price raids a prior swing's resting liquidity (a
stop-hunt that wicks the level and closes back), then a displacement candle
shifts structure in the opposite direction. Entry on the MSS close, stop beyond
the sweep extreme, target the opposing liquidity pool. Concepts §2 / §3.1 / §3.2.

This is a reversal trigger, so it is NOT gated on the (lagging) EMA bias — the
sweep+shift is precisely the event that establishes the new bias.
"""
from __future__ import annotations

from decimal import Decimal

from ..base import BarWindow, Drawing, IndicatorBar, SignalCandidate
from . import primitives as P
from ._base import IctBase, confidence_from_rr, resolve_target, signal_id


class IctSweepMss(IctBase):
    name = "ict_sweep_mss"
    base_confidence = 60

    def _evaluate(self, chrono: list[IndicatorBar], window: BarWindow):
        n = len(chrono)
        latest = chrono[-1]
        atr = latest.atr
        assert atr is not None and latest.close is not None
        swings = P.find_swings(chrono, k=self.swing_k)
        decision = n - 1

        best: SignalCandidate | None = None
        best_rr = Decimal("-1")

        for direction in ("LONG", "SHORT"):
            raid_side = "SSL" if direction == "LONG" else "BSL"
            sweep = P.detect_sweep(
                chrono, swings, end_index=decision, side=raid_side, lookback=self.sweep_lookback
            )
            if sweep is None:
                continue
            broken = P.mss_break(chrono, swings, at_index=decision, direction=direction)
            if broken is None:
                continue

            entry = latest.close
            if direction == "LONG":
                stop = sweep.extreme - self.atr_buffer * atr
                liq = P.nearest_liquidity_above(swings, entry, known_by=decision)
            else:
                stop = sweep.extreme + self.atr_buffer * atr
                liq = P.nearest_liquidity_below(swings, entry, known_by=decision)

            plan = resolve_target(direction, entry, stop, liq.price if liq else None, self.min_rr)
            if plan is None:
                continue
            if plan.rr > best_rr:
                best_rr = plan.rr
                best = self._build(window, latest, sweep, broken, direction, entry, stop, plan, liq)

        return [best] if best is not None else []

    def _build(self, window, latest, sweep, broken, direction, entry, stop, plan, liq):  # noqa: ANN001
        liq_txt = (
            f"opposing liquidity {liq.price}" if plan.source == "liquidity" else f"{self.min_rr}R projection"
        )
        reasoning = (
            f"Swept {sweep.side} at {sweep.level} (wick {sweep.extreme}, "
            f"{sweep.timestamp.isoformat()}) then MSS {direction.lower()} with "
            f"displacement breaking swing {broken.price}. Entry {entry}, SL beyond "
            f"sweep ({stop}), TP {plan.target} via {liq_txt} → RR {plan.rr:.2f}."
        )
        drawings = [
            Drawing("hline", [Drawing._pt(None, sweep.level)], color="#a855f7", label=f"swept {sweep.side}"),
            Drawing(
                "label",
                [Drawing._pt(sweep.timestamp, sweep.extreme)],
                color="#a855f7",
                label="sweep wick",
            ),
            Drawing(
                "line",
                [Drawing._pt(broken.timestamp, broken.price), Drawing._pt(latest.timestamp, broken.price)],
                color="#0ea5e9",
                label="MSS break",
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
