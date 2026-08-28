"""The AI validator is switchable; the risk engine is not.

`ai_validation` off means no model reviews a candidate — no score floor, no AI
veto. Two properties have to hold on that path, and they are the ones most
likely to rot:

  1. The risk engine still runs. It is the mandatory layer (CLAUDE.md), and the
     AI-off path reaches it through the same `_post_ai_gate` as the AI-on path.
  2. The bookkeeping stays honest. Nothing may present the strategy's own
     confidence as a model verdict — the journal has to say the layer was off.
"""
from __future__ import annotations

import pytest

from app.domain.config.defaults import risk_defaults
from app.domain.signals import gate as gate_mod
from app.domain.signals.gate import SignalCandidate

SYMBOL = "XAUUSD"
TIMEFRAME = "5min"

#: The reasoning block the gate builds when the AI layer is switched off.
AI_OFF_LINES = [
    "AI validation: DISABLED (flag `ai_validation` off) — no model reviewed",
    "this signal; the strategy reasoning above is the whole rationale.",
    "Strategy confidence: 62 (not an AI score)",
]


class CollectingSession:
    """Just enough session to let the persist path run without a database."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def candidate(**over) -> SignalCandidate:
    base = {
        "strategyName": "ict_confluence",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": "LONG",
        "entryPrice": 4600.0,
        "stopLoss": 4596.0,
        "takeProfit": 4608.0,
        "confidence": 62,
        "reasoning": "sweep + MSS + FVG in discount, london killzone",
    }
    base.update(over)
    return SignalCandidate(**base)  # type: ignore[arg-type]


@pytest.fixture
def stubbed_tail(monkeypatch: pytest.MonkeyPatch):
    """Stub everything `_post_ai_gate` touches except the risk engine itself."""
    calls: dict[str, object] = {"risk": 0, "executed": 0}

    class Risk:
        approved = True
        reasons: list[str] = []
        positionSize = 25.0

    async def _risk(_session, payload):
        calls["risk"] = int(calls["risk"]) + 1  # type: ignore[call-overload]
        calls["risk_payload"] = payload
        return Risk()

    async def _today_loss(_session):
        return 0.0

    async def _publish(*_a, **_k):
        return None

    monkeypatch.setattr(gate_mod, "validate_trade", _risk)
    monkeypatch.setattr(gate_mod, "_compute_today_loss", _today_loss)
    monkeypatch.setattr(gate_mod, "publish_event", _publish)
    monkeypatch.setattr(
        gate_mod,
        "_read_account_state",
        lambda: {"userId": "system", "accountBalance": 10_000.0, "peakBalance": 10_000.0},
    )
    return calls


async def _run_tail(session, *, ai_score: float, ai_lines: list[str], min_score, shadow: bool):
    return await gate_mod._post_ai_gate(
        session,
        candidate=candidate(),
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        direction="LONG",
        strategy_name="ict_confluence",
        cfg=risk_defaults(),
        upcoming_news=[],
        ai_score=ai_score,
        ai_lines=ai_lines,
        min_score=min_score,
        shadow=shadow,
    )


class TestRiskStillRuns:
    async def test_the_risk_engine_is_called_with_the_ai_layer_off(self, stubbed_tail):
        result = await _run_tail(
            CollectingSession(), ai_score=62, ai_lines=AI_OFF_LINES, min_score=None, shadow=False
        )
        assert result.status == "generated"
        assert stubbed_tail["risk"] == 1, "risk engine must run on the AI-off path"

    async def test_a_risk_rejection_still_blocks_with_the_ai_layer_off(
        self, monkeypatch, stubbed_tail
    ):
        class Rejected:
            approved = False
            reasons = ["rr_below_minimum"]
            positionSize = 0.0

        async def _reject(_session, _payload):
            return Rejected()

        monkeypatch.setattr(gate_mod, "validate_trade", _reject)
        session = CollectingSession()
        result = await _run_tail(
            session, ai_score=62, ai_lines=AI_OFF_LINES, min_score=None, shadow=False
        )
        assert result.status == "rejected"
        assert "rr_below_minimum" in result.reason
        assert session.added == [], "a risk-rejected candidate must not be persisted"


class TestHonestBookkeeping:
    async def test_the_journal_says_the_ai_layer_was_off(self, stubbed_tail):
        session = CollectingSession()
        await _run_tail(
            session, ai_score=62, ai_lines=AI_OFF_LINES, min_score=None, shadow=False
        )
        (signal,) = session.added
        assert "AI validation: DISABLED" in signal.aiReasoning
        assert "not an AI score" in signal.aiReasoning
        # The strategy's own rationale is still the substance of the entry.
        assert "sweep + MSS + FVG in discount" in signal.aiReasoning
        # Nothing invents an AI score line on this path.
        assert "AI score:" not in signal.aiReasoning

    async def test_the_recorded_score_is_the_strategy_confidence(self, stubbed_tail):
        session = CollectingSession()
        result = await _run_tail(
            session, ai_score=62, ai_lines=AI_OFF_LINES, min_score=None, shadow=False
        )
        (signal,) = session.added
        assert signal.confidenceScore == 62
        assert result.score == 62

    async def test_shadow_reports_no_score_floor_when_the_layer_is_off(self, stubbed_tail):
        result = await _run_tail(
            CollectingSession(), ai_score=62, ai_lines=AI_OFF_LINES, min_score=None, shadow=True
        )
        assert result.shadow is not None
        assert result.shadow["minScore"] is None, "no AI floor was applied, so none may be claimed"
        assert result.shadow["score"] == 62

    async def test_the_ai_on_path_still_records_the_model_verdict(self, stubbed_tail):
        session = CollectingSession()
        await _run_tail(
            session,
            ai_score=88,
            ai_lines=["AI score: 88", "AI reasoning: clean structure", "AI concerns: none"],
            min_score=75,
            shadow=False,
        )
        (signal,) = session.added
        assert signal.confidenceScore == 88
        assert "AI score: 88" in signal.aiReasoning
        assert "AI validation: DISABLED" not in signal.aiReasoning
