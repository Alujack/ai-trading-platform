"""Exit evaluation parity, translated from `paperTrading.test.ts`."""
from __future__ import annotations

from app.domain.execution.paper_trading import evaluate_exit


class TestLong:
    def test_returns_none_between_sl_and_tp(self):
        assert evaluate_exit("LONG", 100, 110, 90) is None

    def test_exits_at_tp_when_price_reaches_it(self):
        decision = evaluate_exit("LONG", 110, 110, 90)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (110, "win")

    def test_exits_at_tp_even_when_price_overshoots(self):
        # Clean-fill model: the limit-style TP order fills at the level, not the gap.
        decision = evaluate_exit("LONG", 115, 110, 90)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (110, "win")

    def test_exits_at_sl_when_price_reaches_it(self):
        decision = evaluate_exit("LONG", 90, 110, 90)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (90, "loss")

    def test_exits_at_sl_even_when_price_gaps_below(self):
        decision = evaluate_exit("LONG", 85, 110, 90)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (90, "loss")


class TestShort:
    def test_returns_none_between_tp_and_sl(self):
        # SHORT: TP is below entry, SL is above entry.
        assert evaluate_exit("SHORT", 100, 90, 110) is None

    def test_exits_at_tp_when_price_drops_to_it(self):
        decision = evaluate_exit("SHORT", 90, 90, 110)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (90, "win")

    def test_exits_at_tp_even_when_price_overshoots_downward(self):
        decision = evaluate_exit("SHORT", 85, 90, 110)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (90, "win")

    def test_exits_at_sl_when_price_rises_to_it(self):
        decision = evaluate_exit("SHORT", 110, 90, 110)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (110, "loss")

    def test_exits_at_sl_even_when_price_gaps_above(self):
        decision = evaluate_exit("SHORT", 120, 90, 110)
        assert decision is not None
        assert (decision.exitPrice, decision.outcome) == (110, "loss")
