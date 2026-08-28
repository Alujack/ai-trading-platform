"""In-process AI analysis calls — the replacement for the Node → FastAPI hop.

Phase 3 of the consolidation removes an HTTP round-trip: the gate, the paper
engine, the market-context builder and the news brief now call these functions
directly instead of `fetch(AI_SERVICE_URL + "/analyze/...")`. The prompts,
provider selection and response validation are unchanged — the same
`providers.analyze()` used by the compatibility `/analyze/*` routes.

`providers.analyze` is a blocking call into the vendor SDKs, so every wrapper
here runs it on a worker thread: a 120-second model call must not block the
event loop that is also serving SSE and the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from .prompts import (
    JOURNAL_REVIEW_SYSTEM,
    MARKET_CONTEXT_SYSTEM,
    NEWS_SUMMARY_SYSTEM,
    TRADE_REVIEW_SYSTEM,
    VALIDATE_SIGNAL_SYSTEM,
)
from .providers import analyze
from .schemas import (
    JournalReviewRequest,
    JournalReviewResponse,
    MarketContextRequest,
    MarketContextResponse,
    NewsSummaryRequest,
    NewsSummaryResponse,
    TradeReviewRequest,
    TradeReviewResponse,
    ValidateSignalRequest,
    ValidateSignalResponse,
    serialize_for_prompt,
)

log = logging.getLogger("backend.ai")

M = TypeVar("M", bound=BaseModel)


async def _analyze(system_prompt: str, payload: dict[str, Any], model: type[M]) -> M:
    return await asyncio.to_thread(
        analyze, system_prompt=system_prompt, user_payload=payload, response_model=model
    )


async def market_context(body: MarketContextRequest) -> MarketContextResponse:
    """Structured market-context briefing from price action + news."""
    return await _analyze(MARKET_CONTEXT_SYSTEM, serialize_for_prompt(body), MarketContextResponse)


async def validate_signal(body: ValidateSignalRequest) -> ValidateSignalResponse:
    """Score and approve/reject a proposed trade signal against market context."""
    return await _analyze(
        VALIDATE_SIGNAL_SYSTEM, serialize_for_prompt(body), ValidateSignalResponse
    )


async def journal_review(body: JournalReviewRequest) -> JournalReviewResponse:
    """Behavioural patterns and actionable critique from recent trades."""
    return await _analyze(
        JOURNAL_REVIEW_SYSTEM, serialize_for_prompt(body), JournalReviewResponse
    )


async def trade_review(body: TradeReviewRequest) -> TradeReviewResponse:
    """Grade ONE closed trade on process (not outcome) and extract the lesson."""
    return await _analyze(TRADE_REVIEW_SYSTEM, serialize_for_prompt(body), TradeReviewResponse)


async def news_summary(body: NewsSummaryRequest) -> NewsSummaryResponse:
    """Summarize a batch of headlines and classify impact + affected currency."""
    return await _analyze(NEWS_SUMMARY_SYSTEM, serialize_for_prompt(body), NewsSummaryResponse)
