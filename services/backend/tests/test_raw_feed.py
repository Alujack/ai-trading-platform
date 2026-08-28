"""Raw-feed classification parity, translated from `rawFeed.test.ts`.

The reason strings below are copied verbatim from `gate.py` / `risk/engine.py` —
if either changes its wording, these tests are the tripwire.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db.enums import RawVerdict
from app.domain.signals.gate import SignalCandidate
from app.domain.signals.raw_feed import (
    GateOutcomeClass,
    classify_gate_outcome,
    dedupe_key_for,
)


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def test_marks_a_fully_cleared_candidate_generated():
    assert classify_gate_outcome("generated") == GateOutcomeClass(RawVerdict.GENERATED, None)


@pytest.mark.parametrize(
    ("reason", "blocked_by"),
    [
        ("idempotent_duplicate", "duplicate"),
        ("cooldown_active", "cooldown"),
        ("insufficient_candles=7", "insufficient_candles"),
        ("ai_service_500: upstream boom", "ai_unreachable"),
        ("ai_service_unreachable: fetch failed", "ai_unreachable"),
    ],
)
def test_pre_gate_skips(reason: str, blocked_by: str):
    assert classify_gate_outcome("skipped", reason) == GateOutcomeClass(
        RawVerdict.SKIPPED, blocked_by
    )


def test_separates_a_low_score_from_an_explicit_non_approval():
    assert classify_gate_outcome("rejected", "ai_score_too_low score=42") == GateOutcomeClass(
        RawVerdict.REJECTED, "ai_score"
    )
    assert classify_gate_outcome(
        "rejected", "ai_not_approved: choppy structure"
    ) == GateOutcomeClass(RawVerdict.REJECTED, "ai_judgment")


def test_tags_the_runners_regime_pre_gate_marker():
    assert classify_gate_outcome("rejected", "pre_gated_regime") == GateOutcomeClass(
        RawVerdict.REJECTED, "regime"
    )


@pytest.mark.parametrize(
    ("reason", "blocked_by"),
    [
        ("risk_rejected: entryPrice and stopLoss must differ", "risk_inputs"),
        ("risk_rejected: accountBalance must be a positive number", "risk_inputs"),
        ("risk_rejected: Daily loss limit reached", "risk_daily_loss"),
        ("risk_rejected: Max drawdown exceeded", "risk_drawdown"),
        ("risk_rejected: Risk/reward 1.20 below minimum 2", "risk_rr"),
        ("risk_rejected: Inside news window: US CPI", "risk_news"),
        ("risk_rejected: Inside high-impact news window", "risk_news"),
        ("risk_rejected: Gold concurrent limit: 3/3 positions already open", "risk_gold"),
        ("risk_rejected: something new nobody mapped", "risk"),
    ],
)
def test_risk_engine_sub_layers(reason: str, blocked_by: str):
    assert classify_gate_outcome("rejected", reason) == GateOutcomeClass(
        RawVerdict.REJECTED, blocked_by
    )


def test_reports_the_first_reason_when_several_fire_at_once():
    # validate_trade pushes in order: inputs → daily loss → drawdown → RR → news.
    joined = "risk_rejected: Daily loss limit reached; Risk/reward 1.20 below minimum 2"
    assert classify_gate_outcome("rejected", joined).blockedBy == "risk_daily_loss"


def test_falls_back_to_unknown_rather_than_guessing_a_layer():
    assert classify_gate_outcome("skipped", "brand_new_reason") == GateOutcomeClass(
        RawVerdict.SKIPPED, "unknown"
    )
    assert classify_gate_outcome("skipped") == GateOutcomeClass(RawVerdict.SKIPPED, "unknown")


CAND = SignalCandidate(
    strategyName="sweep_mss",
    symbol="XAUUSD",
    timeframe="60min",
    direction="LONG",
    entryPrice=2400.5,
    stopLoss=2395.5,
    takeProfit=2412.5,
    confidence=60,
    reasoning="sweep + MSS",
)


class TestDedupeKey:
    NOW = at("2026-08-28T12:00:00Z")

    def test_prefers_the_strategys_own_per_bar_client_id(self):
        assert dedupe_key_for(replace(CAND, clientId="bar-123"), self.NOW) == "cid:bar-123"

    def test_collapses_an_identical_reproposal_on_the_same_utc_day(self):
        a = dedupe_key_for(CAND, self.NOW)
        b = dedupe_key_for(replace(CAND), at("2026-08-28T23:59:00Z"))
        assert a == b

    def test_separates_different_levels_and_later_days(self):
        assert dedupe_key_for(replace(CAND, takeProfit=2420), self.NOW) != dedupe_key_for(
            CAND, self.NOW
        )
        assert dedupe_key_for(CAND, at("2026-08-29T00:00:00Z")) != dedupe_key_for(CAND, self.NOW)


# The raw feed's whole safety argument is that nothing on the money path can see
# it. This test fails the build if that ever stops being true.
def test_raw_feed_is_unreachable_from_execution():
    execution_dir = Path(__file__).resolve().parents[1] / "app" / "domain" / "execution"
    offenders = [
        path.name
        for path in execution_dir.rglob("*.py")
        if "raw_feed" in path.read_text("utf-8") or "RawSignal" in path.read_text("utf-8")
    ]
    assert offenders == []
