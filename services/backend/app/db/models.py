"""SQLAlchemy 2 models over the existing Prisma-created schema.

Nothing is renamed. Table names stay PascalCase and columns stay camelCase
exactly as `apps/api/prisma/migrations` created them, so both runtimes can read
and write the same rows during the migration window. `TIMESTAMP(3)` columns are
timezone-naive UTC, matching Prisma.

Ids are supplied by the application (`core.ids.new_id`) because Prisma's
`cuid()` default is client-side and the columns carry no database default.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import (
    ApprovalStatus,
    Direction,
    ExecutionMode,
    Impact,
    RawVerdict,
    SignalStatus,
    TradeStatus,
)


class Base(DeclarativeBase):
    """Declarative base for the trading schema."""


def _enum(python_type: type, name: str) -> SAEnum:
    """A Postgres enum column bound to a type the migrations already created."""
    return SAEnum(
        python_type,
        name=name,
        create_type=False,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
    )


# Prisma writes `Json` columns as jsonb.
_Json = JSONB
# `TIMESTAMP(3)` — naive UTC, millisecond precision.
_Ts = DateTime(timezone=False)


class Candle(Base):
    __tablename__ = "Candle"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="Candle_symbol_timeframe_timestamp_key"),
        Index("Candle_symbol_timeframe_timestamp_idx", "symbol", "timeframe", "timestamp"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(_Ts, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class Indicator(Base):
    __tablename__ = "Indicator"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "timeframe", "timestamp", name="Indicator_symbol_timeframe_timestamp_key"
        ),
        Index("Indicator_symbol_timeframe_timestamp_idx", "symbol", "timeframe", "timestamp"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(_Ts, nullable=False)
    rsi: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ema20: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    ema50: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    ema200: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    atr: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    bbLower: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    bbUpper: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    bbPctB: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    adx: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class NewsEvent(Base):
    __tablename__ = "NewsEvent"
    __table_args__ = (
        UniqueConstraint("title", "scheduledAt", name="NewsEvent_title_scheduledAt_key"),
        Index("NewsEvent_scheduledAt_idx", "scheduledAt"),
        Index("NewsEvent_currency_idx", "currency"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[Impact] = mapped_column(_enum(Impact, "Impact"), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    scheduledAt: Mapped[datetime] = mapped_column(_Ts, nullable=False)
    actual: Mapped[str | None] = mapped_column(Text)
    forecast: Mapped[str | None] = mapped_column(Text)
    previous: Mapped[str | None] = mapped_column(Text)
    aiSummary: Mapped[str | None] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class Strategy(Base):
    __tablename__ = "Strategy"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    regimes: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(_Json, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class Signal(Base):
    __tablename__ = "Signal"
    __table_args__ = (
        Index("Signal_symbol_status_idx", "symbol", "status"),
        Index("Signal_strategyName_idx", "strategyName"),
        Index("Signal_createdAt_idx", "createdAt"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[Direction] = mapped_column(_enum(Direction, "Direction"), nullable=False)
    entryPrice: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    stopLoss: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    takeProfit: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    confidenceScore: Mapped[int] = mapped_column(Integer, nullable=False)
    aiReasoning: Mapped[str] = mapped_column(Text, nullable=False)
    strategyName: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SignalStatus] = mapped_column(
        _enum(SignalStatus, "SignalStatus"), nullable=False, default=SignalStatus.PENDING
    )
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())

    trades: Mapped[list["Trade"]] = relationship(back_populates="signal", lazy="selectin")
    approval: Mapped["Approval | None"] = relationship(back_populates="signal", lazy="selectin")


class Trade(Base):
    __tablename__ = "Trade"
    __table_args__ = (
        Index("Trade_signalId_idx", "signalId"),
        Index("Trade_status_idx", "status"),
        Index("Trade_externalOrderId_idx", "externalOrderId"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    signalId: Mapped[str] = mapped_column(Text, ForeignKey("Signal.id"), nullable=False)
    entryPrice: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exitPrice: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    positionSize: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    riskAmount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    profitLoss: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    status: Mapped[TradeStatus] = mapped_column(
        _enum(TradeStatus, "TradeStatus"), nullable=False, default=TradeStatus.OPEN
    )
    openedAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())
    closedAt: Mapped[datetime | None] = mapped_column(_Ts)
    externalOrderId: Mapped[str | None] = mapped_column(Text)
    brokerFillPrice: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    broker: Mapped[str | None] = mapped_column(Text)

    signal: Mapped[Signal] = relationship(back_populates="trades", lazy="selectin")
    journals: Mapped[list["Journal"]] = relationship(back_populates="trade", lazy="selectin")


class Journal(Base):
    __tablename__ = "Journal"
    __table_args__ = (Index("Journal_tradeId_idx", "tradeId"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tradeId: Mapped[str] = mapped_column(Text, ForeignKey("Trade.id"), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    aiReview: Mapped[str] = mapped_column(Text, nullable=False)
    emotions: Mapped[str | None] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    lesson: Mapped[str | None] = mapped_column(Text)
    rMultiple: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())

    trade: Mapped[Trade] = relationship(back_populates="journals", lazy="selectin")


class RiskLog(Base):
    __tablename__ = "RiskLog"
    __table_args__ = (Index("RiskLog_createdAt_idx", "createdAt"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    accountBalance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    riskPercent: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    positionSize: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    dailyLoss: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    dailyLossLimit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    circuitBreakerTripped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class RiskConfig(Base):
    __tablename__ = "RiskConfig"
    __table_args__ = (UniqueConstraint("scope", "scopeKey", name="RiskConfig_scope_scopeKey_key"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scopeKey: Mapped[str] = mapped_column(Text, nullable=False)
    riskPerTradePct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    minRR: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    dailyLossLimitPct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    dailyProfitTargetPct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    maxDrawdownPct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    maxOpenTrades: Mapped[int | None] = mapped_column(Integer)
    maxTradesPerDay: Mapped[int | None] = mapped_column(Integer)
    maxOpenRiskPct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    maxRiskPerCurrencyPct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    newsBeforeMin: Mapped[int | None] = mapped_column(Integer)
    newsAfterMin: Mapped[int | None] = mapped_column(Integer)
    aiMinScore: Mapped[int | None] = mapped_column(Integer)
    approvalTtlMin: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updatedAt: Mapped[datetime] = mapped_column(
        _Ts, nullable=False, default=func.now(), onupdate=func.now()
    )


class ExecutionSetting(Base):
    __tablename__ = "ExecutionSetting"
    __table_args__ = (
        UniqueConstraint("scope", "scopeKey", name="ExecutionSetting_scope_scopeKey_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scopeKey: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[ExecutionMode] = mapped_column(
        _enum(ExecutionMode, "ExecutionMode"), nullable=False, default=ExecutionMode.CONFIRM
    )
    updatedAt: Mapped[datetime] = mapped_column(
        _Ts, nullable=False, default=func.now(), onupdate=func.now()
    )


class ConfigAudit(Base):
    __tablename__ = "ConfigAudit"
    __table_args__ = (Index("ConfigAudit_createdAt_idx", "createdAt"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scopeKey: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict] = mapped_column(_Json, nullable=False)
    after: Mapped[dict] = mapped_column(_Json, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class BacktestRun(Base):
    __tablename__ = "BacktestRun"
    __table_args__ = (Index("BacktestRun_createdAt_idx", "createdAt"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str | None] = mapped_column(Text)
    startingBalance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    riskPct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    costsApplied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict] = mapped_column(_Json, nullable=False)
    results: Mapped[dict] = mapped_column(_Json, nullable=False)
    equityCurves: Mapped[dict | None] = mapped_column(_Json)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class BrokerCredential(Base):
    __tablename__ = "BrokerCredential"
    __table_args__ = (Index("BrokerCredential_isActive_idx", "isActive"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    broker: Mapped[str] = mapped_column(Text, nullable=False, default="exness")
    login: Mapped[int] = mapped_column(Integer, nullable=False)
    passwordEnc: Mapped[str] = mapped_column(Text, nullable=False)
    server: Mapped[str] = mapped_column(Text, nullable=False)
    env: Mapped[str] = mapped_column(Text, nullable=False, default="demo")
    isActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    lastTest: Mapped[dict | None] = mapped_column(_Json)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(
        _Ts, nullable=False, default=func.now(), onupdate=func.now()
    )


class AgentRecommendation(Base):
    __tablename__ = "AgentRecommendation"
    __table_args__ = (
        Index("AgentRecommendation_status_idx", "status"),
        Index("AgentRecommendation_expiresAt_idx", "expiresAt"),
        Index("AgentRecommendation_createdAt_idx", "createdAt"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scopeKey: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    currentValue: Mapped[object] = mapped_column(_Json, nullable=False)
    proposedValue: Mapped[object] = mapped_column(_Json, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus, "ApprovalStatus"), nullable=False, default=ApprovalStatus.PENDING
    )
    chatId: Mapped[str | None] = mapped_column(Text)
    messageId: Mapped[str | None] = mapped_column(Text)
    decidedBy: Mapped[str | None] = mapped_column(Text)
    decidedAt: Mapped[datetime | None] = mapped_column(_Ts)
    appliedAt: Mapped[datetime | None] = mapped_column(_Ts)
    expiresAt: Mapped[datetime] = mapped_column(_Ts, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class Approval(Base):
    __tablename__ = "Approval"
    __table_args__ = (
        Index("Approval_status_idx", "status"),
        Index("Approval_expiresAt_idx", "expiresAt"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    signalId: Mapped[str] = mapped_column(
        Text, ForeignKey("Signal.id"), nullable=False, unique=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus, "ApprovalStatus"), nullable=False, default=ApprovalStatus.PENDING
    )
    chatId: Mapped[str] = mapped_column(Text, nullable=False)
    messageId: Mapped[str | None] = mapped_column(Text)
    decidedBy: Mapped[str | None] = mapped_column(Text)
    decidedAt: Mapped[datetime | None] = mapped_column(_Ts)
    expiresAt: Mapped[datetime] = mapped_column(_Ts, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())

    signal: Mapped[Signal] = relationship(back_populates="approval", lazy="selectin")


class RawSignal(Base):
    """The pure strategy feed — see the Prisma model's safety notes.

    Deliberately an island: no relationship into `Trade`/`Approval`, and nothing
    under `domain/execution` reads it, so a raw row can never become a position.
    """

    __tablename__ = "RawSignal"
    __table_args__ = (
        Index("RawSignal_createdAt_idx", "createdAt"),
        Index("RawSignal_strategyName_idx", "strategyName"),
        Index("RawSignal_symbol_timeframe_idx", "symbol", "timeframe"),
        Index("RawSignal_verdict_idx", "verdict"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[Direction] = mapped_column(_enum(Direction, "Direction"), nullable=False)
    entryPrice: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    stopLoss: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    takeProfit: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    strategyName: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[RawVerdict] = mapped_column(
        _enum(RawVerdict, "RawVerdict"), nullable=False, default=RawVerdict.PENDING
    )
    blockedBy: Mapped[str | None] = mapped_column(Text)
    blockedReason: Mapped[str | None] = mapped_column(Text)
    signalId: Mapped[str | None] = mapped_column(Text)
    dedupeKey: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    seenCount: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lastSeenAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())
    createdAt: Mapped[datetime] = mapped_column(_Ts, nullable=False, server_default=func.now())


class FeatureFlag(Base):
    __tablename__ = "FeatureFlag"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updatedAt: Mapped[datetime] = mapped_column(
        _Ts, nullable=False, default=func.now(), onupdate=func.now()
    )


__all__ = [
    "Base",
    "Candle",
    "Indicator",
    "NewsEvent",
    "Strategy",
    "Signal",
    "Trade",
    "Journal",
    "RiskLog",
    "RiskConfig",
    "ExecutionSetting",
    "ConfigAudit",
    "BacktestRun",
    "BrokerCredential",
    "AgentRecommendation",
    "Approval",
    "RawSignal",
    "FeatureFlag",
]
