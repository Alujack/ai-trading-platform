"""End-to-end paper cycle against a real database — the Phase 6 exit gate.

"Paper trading completes full open→manage→close→journal cycles."

This is the only test that needs Postgres. It refuses to run unless
`TEST_DATABASE_URL` is set AND names a database ending in `_test`, so it can
never truncate a real trading database by accident. Set it up with:

    docker exec trading-postgres psql -U postgres -c 'CREATE DATABASE trading_test;'
    cd services/backend && DATABASE_URL=postgresql://postgres:postgres@localhost:55432/trading_test \\
        .venv/bin/alembic upgrade head
    export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/trading_test
"""
from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.ids import new_id
from app.core.settings import get_settings
from app.db.enums import Direction, ExecutionMode, SignalStatus, TradeStatus
from app.db.models import Candle, Journal, Signal, Trade
from app.jobs.clock import naive_utcnow

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_URL or not TEST_URL.rstrip("/").endswith("_test"),
    reason="TEST_DATABASE_URL not set to a *_test database — skipping integration cycle",
)

#: Tables this test writes to and clears between runs.
OWNED_TABLES = (
    "Journal",
    "Trade",
    "Approval",
    "Signal",
    "RiskLog",
    "Candle",
    "Indicator",
    "ExecutionSetting",
    "RiskConfig",
    "ConfigAudit",
    "FeatureFlag",
    "RawSignal",
)

SYMBOL = "XAUUSD"
TIMEFRAME = "60min"
ENTRY = 4000.0
STOP = 3995.0
TARGET = 4010.0  # RR = 2.0 exactly


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch, mock_ai_provider):
    """A clean session against the test database, with settings pointed at it.

    Depends on `mock_ai_provider` so the cycle exercises the gate rather than
    whichever LLM key happens to be in the developer's `.env`.
    """
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("PAPER_ACCOUNT_BALANCE", "10000")
    monkeypatch.setenv("PAPER_PEAK_BALANCE", "10000")
    monkeypatch.setenv("PAPER_RISK_PERCENT", "1")
    monkeypatch.setenv("API_SHADOW_MODE", "false")
    get_settings.cache_clear()

    engine = create_async_engine(_async_url(TEST_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for table in OWNED_TABLES:
            await session.execute(text(f'TRUNCATE TABLE "{table}" CASCADE'))
        await session.commit()
        yield session
    await engine.dispose()
    get_settings.cache_clear()


def _bar(close: float, ts) -> Candle:
    return Candle(
        id=new_id(),
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open=Decimal(str(close)),
        high=Decimal(str(close + 2)),
        low=Decimal(str(close - 2)),
        close=Decimal(str(close)),
        volume=Decimal(1000),
        timestamp=ts,
        createdAt=naive_utcnow(),
    )


async def seed_candles(session, close: float, count: int = 20) -> None:
    """Enough bars for the gate's `insufficient_candles` floor (needs >= 10)."""
    now = naive_utcnow().replace(minute=0, second=0, microsecond=0)
    for i in range(count):
        session.add(_bar(close, now - timedelta(hours=count - i)))
    await session.commit()


async def print_bar(session, close: float) -> None:
    """Append ONE bar strictly newer than every existing one.

    `fetch_current_price` marks positions to the newest bar, so the closing print
    has to sort last — reusing a seeded timestamp would collide with the
    (symbol, timeframe, timestamp) unique index instead of moving the price.
    """
    from sqlalchemy import func, select

    newest = (
        await session.execute(
            select(func.max(Candle.timestamp)).where(
                Candle.symbol == SYMBOL, Candle.timeframe == TIMEFRAME
            )
        )
    ).scalar()
    base = newest or naive_utcnow().replace(minute=0, second=0, microsecond=0)
    session.add(_bar(close, base + timedelta(hours=1)))
    await session.commit()


async def set_mode(session, mode: ExecutionMode) -> None:
    from app.domain.config.store import write_execution_mode

    result = await write_execution_mode(session, "test", "GLOBAL", "", mode)
    assert result.ok, result.error


def candidate():
    from app.domain.signals.gate import SignalCandidate

    return SignalCandidate(
        strategyName="integration_probe",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        direction="LONG",
        entryPrice=ENTRY,
        stopLoss=STOP,
        takeProfit=TARGET,
        confidence=70,
        reasoning="integration cycle probe",
    )


class TestFullPaperCycle:
    async def test_open_manage_close_journal(self, db):
        from sqlalchemy import select

        from app.domain.execution.paper_trading import monitor_open_trades
        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY)
        await set_mode(db, ExecutionMode.AUTO)

        # --- gate: AI + risk approve, Signal persisted, AUTO opens the trade ---
        result = await gate_candidate(db, candidate())
        assert result.status == "generated", result.reason
        assert result.signalId

        signal = (
            await db.execute(select(Signal).where(Signal.id == result.signalId))
        ).scalar_one()
        assert signal.status == SignalStatus.ACTIVE  # AUTO flipped it on open
        assert signal.direction == Direction.LONG
        assert signal.strategyName == "integration_probe"

        trades = (await db.execute(select(Trade))).scalars().all()
        assert len(trades) == 1
        trade = trades[0]
        assert trade.status == TradeStatus.OPEN
        # risk$ = 1% of 10_000 = 100; stop distance = 5 → size 20 units
        assert float(trade.positionSize) == pytest.approx(20.0)
        assert float(trade.riskAmount) == pytest.approx(100.0)

        # The risk engine must have left an audit row.
        risk_logs = (await db.execute(text('SELECT count(*) FROM "RiskLog"'))).scalar()
        assert risk_logs >= 1

        # --- manage: price still mid-range, nothing closes ---
        summary = await monitor_open_trades(db)
        assert summary.inspected == 1
        assert summary.closed == 0
        assert summary.unchanged == 1

        # --- close: a bar prints at the target ---
        await print_bar(db, TARGET)
        db.expire_all()
        summary = await monitor_open_trades(db)
        assert summary.closed == 1

        closed = (await db.execute(select(Trade).where(Trade.id == trade.id))).scalar_one()
        assert closed.status == TradeStatus.CLOSED
        assert closed.closedAt is not None
        assert float(closed.exitPrice) == pytest.approx(TARGET)
        # (4010 - 4000) * 20 units = +200
        assert float(closed.profitLoss) == pytest.approx(200.0)

        # --- journal: every closed trade gets one (CLAUDE.md rule) ---
        journals = (await db.execute(select(Journal).where(Journal.tradeId == trade.id))).scalars().all()
        assert len(journals) == 1
        journal = journals[0]
        assert journal.outcome == "WIN"
        # R-multiple = 200 / 100 risk = 2.0
        assert float(journal.rMultiple) == pytest.approx(2.0)
        assert "Auto-closed by paper trading engine" in journal.notes
        assert journal.aiReview  # populated even when the AI review is unavailable

        # The parent signal is closed out too.
        signal = (await db.execute(select(Signal).where(Signal.id == signal.id))).scalar_one()
        assert signal.status == SignalStatus.CLOSED

    async def test_stop_loss_exit_is_journaled_as_a_loss(self, db):
        from sqlalchemy import select

        from app.domain.execution.paper_trading import monitor_open_trades
        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY)
        await set_mode(db, ExecutionMode.AUTO)
        result = await gate_candidate(db, candidate())
        assert result.status == "generated", result.reason

        await print_bar(db, STOP)
        db.expire_all()
        assert (await monitor_open_trades(db)).closed == 1

        trade = (await db.execute(select(Trade))).scalars().one()
        assert float(trade.profitLoss) == pytest.approx(-100.0)  # exactly 1R
        journal = (await db.execute(select(Journal))).scalars().one()
        assert journal.outcome == "LOSS"
        assert float(journal.rMultiple) == pytest.approx(-1.0)


class TestModeGating:
    async def test_off_mode_leaves_the_signal_pending_with_no_trade(self, db):
        from sqlalchemy import select

        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY)
        await set_mode(db, ExecutionMode.OFF)

        result = await gate_candidate(db, candidate())
        assert result.status == "generated"  # the signal is still recorded...

        signal = (await db.execute(select(Signal))).scalars().one()
        assert signal.status == SignalStatus.PENDING  # ...but nothing opened
        assert (await db.execute(select(Trade))).scalars().all() == []

    async def test_confirm_mode_creates_an_approval_not_a_trade(self, db):
        from sqlalchemy import select

        from app.db.models import Approval
        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY)
        await set_mode(db, ExecutionMode.CONFIRM)

        result = await gate_candidate(db, candidate())
        assert result.status == "generated"

        approvals = (await db.execute(select(Approval))).scalars().all()
        assert len(approvals) == 1
        assert approvals[0].signalId == result.signalId
        assert (await db.execute(select(Trade))).scalars().all() == []


class TestIdempotencyAndCooldown:
    async def test_a_repeated_client_id_never_creates_a_second_signal(self, db):
        from dataclasses import replace

        from sqlalchemy import select

        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY)
        await set_mode(db, ExecutionMode.OFF)

        cand = replace(candidate(), clientId="bar-2026-08-28T10")
        first = await gate_candidate(db, cand)
        assert first.status == "generated"
        assert first.signalId == "bar-2026-08-28T10"

        retry = await gate_candidate(db, cand)
        assert retry.status == "skipped"
        assert retry.reason == "idempotent_duplicate"

        assert len((await db.execute(select(Signal))).scalars().all()) == 1

    async def test_cooldown_suppresses_a_fresh_signal_while_one_is_open(self, db):
        from dataclasses import replace

        from sqlalchemy import select

        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY)
        await set_mode(db, ExecutionMode.OFF)

        assert (await gate_candidate(db, candidate())).status == "generated"
        second = await gate_candidate(db, replace(candidate(), cooldownMs=3_600_000))
        assert second.status == "skipped"
        assert second.reason == "cooldown_active"
        assert len((await db.execute(select(Signal))).scalars().all()) == 1

    async def test_thin_candle_history_is_skipped_not_traded(self, db):
        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY, count=5)  # below the 10-bar floor
        await set_mode(db, ExecutionMode.AUTO)

        result = await gate_candidate(db, candidate())
        assert result.status == "skipped"
        assert result.reason == "insufficient_candles=5"


class TestRiskRejection:
    async def test_a_sub_minimum_rr_candidate_is_rejected_and_logged(self, db):
        from dataclasses import replace

        from sqlalchemy import select

        from app.domain.signals.gate import gate_candidate

        await seed_candles(db, ENTRY)
        await set_mode(db, ExecutionMode.AUTO)

        # RR 1.0 — below the default minimum of 2.
        bad = replace(candidate(), takeProfit=ENTRY + 5.0)
        result = await gate_candidate(db, bad)
        assert result.status == "rejected"
        assert "risk_rejected" in (result.reason or "")
        assert "Risk/reward" in (result.reason or "")

        # No signal, no trade — but the risk decision IS logged.
        assert (await db.execute(select(Signal))).scalars().all() == []
        risk_logs = (await db.execute(text('SELECT count(*) FROM "RiskLog"'))).scalar()
        assert risk_logs == 1
