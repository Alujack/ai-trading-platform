"""Pydantic models for analysis endpoint requests and responses."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


_PASS_THROUGH = ConfigDict(extra="allow")


class Candle(BaseModel):
    """OHLCV bar. Permissive — accepts extra fields from the API layer."""

    model_config = _PASS_THROUGH

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class Indicator(BaseModel):
    """Indicator row keyed on the same timestamp scheme as Candle."""

    model_config = _PASS_THROUGH

    timestamp: str
    rsi: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    atr: float | None = None


class NewsEvent(BaseModel):
    model_config = _PASS_THROUGH

    title: str
    impact: Literal["LOW", "MEDIUM", "HIGH"] | str
    currency: str
    scheduled_at: str = Field(..., alias="scheduledAt")
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None


class SignalInput(BaseModel):
    """Trade signal under review."""

    model_config = _PASS_THROUGH

    symbol: str
    timeframe: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float = Field(..., alias="entryPrice")
    stop_loss: float = Field(..., alias="stopLoss")
    take_profit: float = Field(..., alias="takeProfit")
    confidence_score: int | None = Field(default=None, alias="confidenceScore")
    ai_reasoning: str | None = Field(default=None, alias="aiReasoning")


class JournalTrade(BaseModel):
    """One trade + its journal entry, as supplied to /analyze/journal-review."""

    model_config = _PASS_THROUGH

    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float = Field(..., alias="entryPrice")
    exit_price: float | None = Field(default=None, alias="exitPrice")
    profit_loss: float | None = Field(default=None, alias="profitLoss")
    opened_at: str = Field(..., alias="openedAt")
    closed_at: str | None = Field(default=None, alias="closedAt")
    notes: str
    emotions: str | None = None
    ai_review: str | None = Field(default=None, alias="aiReview")


# ---- Request bodies -----------------------------------------------------------


class MarketContextRequest(BaseModel):
    symbol: str
    timeframe: str
    candles: list[Candle] = Field(..., max_length=200)
    indicators: list[Indicator] = Field(..., max_length=50)
    news: list[NewsEvent] = Field(default_factory=list, max_length=20)


class ValidateSignalRequest(BaseModel):
    signal: SignalInput
    candles: list[Candle] = Field(..., max_length=200)
    indicators: list[Indicator] = Field(..., max_length=50)
    upcoming_news: list[NewsEvent] = Field(
        default_factory=list,
        max_length=20,
        alias="upcomingNews",
    )

    model_config = ConfigDict(populate_by_name=True)


class JournalReviewRequest(BaseModel):
    trades: list[JournalTrade] = Field(..., min_length=1, max_length=100)


class TradeReviewInput(BaseModel):
    """One CLOSED trade, with its original plan, for per-trade post-mortem."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    symbol: str
    direction: Literal["LONG", "SHORT"]
    strategy_name: str | None = Field(default=None, alias="strategyName")
    entry_price: float = Field(..., alias="entryPrice")
    stop_loss: float | None = Field(default=None, alias="stopLoss")
    take_profit: float | None = Field(default=None, alias="takeProfit")
    exit_price: float | None = Field(default=None, alias="exitPrice")
    profit_loss: float | None = Field(default=None, alias="profitLoss")
    r_multiple: float | None = Field(default=None, alias="rMultiple")
    exit_reason: str | None = Field(default=None, alias="exitReason")
    opened_at: str = Field(..., alias="openedAt")
    closed_at: str | None = Field(default=None, alias="closedAt")
    planned_reasoning: str | None = Field(default=None, alias="plannedReasoning")


class TradeReviewRequest(BaseModel):
    """A single closed trade + optional price context for /analyze/trade-review."""

    model_config = ConfigDict(populate_by_name=True)

    trade: TradeReviewInput
    candles: list[Candle] = Field(default_factory=list, max_length=200)
    indicators: list[Indicator] = Field(default_factory=list, max_length=50)


class Headline(BaseModel):
    """One published news item supplied to /analyze/news-summary."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    title: str
    source: str | None = None
    published_at: str | None = Field(default=None, alias="publishedAt")
    body: str | None = None


class NewsSummaryRequest(BaseModel):
    headlines: list[Headline] = Field(..., min_length=1, max_length=25)


# ---- Response bodies ----------------------------------------------------------


Bias = Literal["Bullish", "Bearish", "Neutral"]


class MarketContextResponse(BaseModel):
    """Output schema for /analyze/market-context."""

    model_config = ConfigDict(extra="forbid")

    bias: Bias
    summary: str
    keyLevels: list[str]  # noqa: N815 — wire format is camelCase per spec
    risks: list[str]


class ValidateSignalResponse(BaseModel):
    """Output schema for /analyze/validate-signal."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(..., ge=0, le=100)
    approved: bool
    reasoning: str
    concerns: list[str]


class JournalReviewResponse(BaseModel):
    """Output schema for /analyze/journal-review."""

    model_config = ConfigDict(extra="forbid")

    patterns: list[str]
    strengths: list[str]
    weaknesses: list[str]
    suggestions: list[str]


class TradeReviewResponse(BaseModel):
    """Output schema for /analyze/trade-review — a per-trade post-mortem.

    `grade` scores the PROCESS, not the outcome: a winning trade taken against
    the plan can still earn a low grade, and a disciplined loss (followed the
    plan, stopped fairly) can grade well. This keeps the agent learning the
    behavior that compounds, not just chasing green P&L.
    """

    model_config = ConfigDict(extra="forbid")

    grade: Literal["A", "B", "C", "D", "F"]
    outcome: Literal["WIN", "LOSS", "BREAKEVEN"]
    why: str
    whatWorked: list[str]  # noqa: N815 — wire format is camelCase
    whatFailed: list[str]  # noqa: N815
    lesson: str


class NewsSummaryResponse(BaseModel):
    """Output schema for /analyze/news-summary.

    `impact` and `currency` are shaped to drop straight into NewsEvent rows;
    the risk engine acts only on HIGH-impact events, so impact fidelity matters.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    impact: Literal["LOW", "MEDIUM", "HIGH"]
    currency: str
    rationale: str


# ---- Prompt-input serialization -----------------------------------------------


def serialize_for_prompt(payload: BaseModel) -> dict[str, Any]:
    """Render a request body as a dict suitable for inlining in a user message.

    Uses `by_alias=True` so the model sees camelCase field names that match
    the upstream API contract (no leaky `entry_price` snake_case in prompts).
    """
    return payload.model_dump(by_alias=True, mode="json", exclude_none=True)
