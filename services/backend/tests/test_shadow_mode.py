"""Shadow mode must be strictly read-only.

Plan §4 invariant 3: "Only one runtime may execute trades. Shadow services are
read-only and must not dual-write." A shadow instance runs beside the real writer
to prove decision parity, so anything it persists would be a duplicate row.

These tests pin that behaviour on every write path a candidate can reach.
"""
from __future__ import annotations

import pytest

from app.core.settings import get_settings
from app.domain.risk.engine import (
    RiskThresholds,
    ValidateTradeInput,
    validate_trade,
)


class RecordingSession:
    """Captures anything a code path tries to persist."""

    def __init__(self) -> None:
        self.added: list = []
        self.flushed = 0
        self.committed = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        return None

    async def execute(self, *_a, **_k):
        raise AssertionError("shadow mode should not query in this test")


def trade_input(**over) -> ValidateTradeInput:
    base = {
        "userId": "system",
        "symbol": "XAUUSD",
        "entry": 2400.0,
        "stopLoss": 2395.0,
        "takeProfit": 2410.0,
        "accountBalance": 10_000.0,
        "peakBalance": 10_000.0,
        "todayLoss": 0.0,
        "riskPercent": 1.0,
        "upcomingNews": [],
        "thresholds": RiskThresholds(),
    }
    base.update(over)
    return ValidateTradeInput(**base)  # type: ignore[arg-type]


@pytest.fixture
def shadow(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_SHADOW_MODE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_SHADOW_MODE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestRiskLogPersistence:
    async def test_live_mode_writes_a_risk_log_row(self, live):
        session = RecordingSession()
        result = await validate_trade(session, trade_input())
        assert result.approved is True
        assert len(session.added) == 1
        assert type(session.added[0]).__name__ == "RiskLog"

    async def test_live_mode_writes_a_risk_log_even_when_rejected(self, live):
        # "RiskLog persistence even when a candidate is rejected" (plan §9).
        session = RecordingSession()
        result = await validate_trade(session, trade_input(takeProfit=2401.0))
        assert result.approved is False
        assert len(session.added) == 1
        assert session.added[0].circuitBreakerTripped is True

    async def test_shadow_mode_writes_nothing(self, shadow):
        session = RecordingSession()
        result = await validate_trade(session, trade_input())
        # The verdict is still computed and returned...
        assert result.approved is True
        assert result.positionSize == pytest.approx(20.0)
        # ...but nothing is persisted.
        assert session.added == []
        assert session.flushed == 0

    async def test_shadow_mode_still_reports_a_rejection(self, shadow):
        session = RecordingSession()
        result = await validate_trade(session, trade_input(takeProfit=2401.0))
        assert result.approved is False
        assert any("Risk/reward" in r for r in result.reasons)
        assert session.added == []


class TestExecutionOpeners:
    async def test_paper_open_refuses_in_shadow_mode(self, shadow):
        from app.domain.execution.paper_trading import open_paper_trade

        result = await open_paper_trade(RecordingSession(), "any-signal-id")
        assert result.status == "skipped"
        assert result.reason == "shadow_mode_no_execution"

    async def test_live_open_refuses_in_shadow_mode(self, shadow):
        from app.domain.execution.live_trade import open_live_trade

        result = await open_live_trade(RecordingSession(), "any-signal-id")
        assert result.status == "skipped"
        assert result.reason == "shadow_mode_no_execution"

    async def test_approval_request_refuses_in_shadow_mode(self, shadow):
        from app.integrations.telegram.approvals import request_approval

        result = await request_approval(RecordingSession(), object(), 15)  # type: ignore[arg-type]
        assert result["created"] is False
        assert result["reason"] == "shadow_mode_no_telegram"


class TestSchedulers:
    def test_shadow_mode_keeps_the_execution_jobs_stopped(self, shadow):
        from app.jobs.scheduler import scheduler_state, start_schedulers, stop_schedulers

        start_schedulers()
        try:
            assert scheduler_state()["running"] == []
        finally:
            stop_schedulers()
