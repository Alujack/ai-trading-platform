"""`/analyze/*` compatibility routes.

These are the AI service's original endpoints, kept so external callers (the n8n
news workflow, any script pointed at `AI_SERVICE_URL`) keep working after
`services/ai` is folded into this backend. Internal callers no longer come
through here — they call `integrations.ai.client` directly, removing the HTTP hop.
"""
from __future__ import annotations

from fastapi import APIRouter

from ...integrations.ai import client as ai
from ...integrations.ai.schemas import (
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
)

router = APIRouter(prefix="/analyze", tags=["analyze"])


@router.post("/market-context", response_model=MarketContextResponse)
async def market_context(body: MarketContextRequest) -> MarketContextResponse:
    """Produce a structured market-context briefing from price action + news."""
    return await ai.market_context(body)


@router.post("/validate-signal", response_model=ValidateSignalResponse)
async def validate_signal(body: ValidateSignalRequest) -> ValidateSignalResponse:
    """Score and approve/reject a proposed trade signal against market context."""
    return await ai.validate_signal(body)


@router.post("/journal-review", response_model=JournalReviewResponse)
async def journal_review(body: JournalReviewRequest) -> JournalReviewResponse:
    """Extract behavioral patterns and actionable critique from recent trades."""
    return await ai.journal_review(body)


@router.post("/trade-review", response_model=TradeReviewResponse)
async def trade_review(body: TradeReviewRequest) -> TradeReviewResponse:
    """Grade ONE closed trade on process (not outcome) and explain why it won/lost."""
    return await ai.trade_review(body)


@router.post("/news-summary", response_model=NewsSummaryResponse)
async def news_summary(body: NewsSummaryRequest) -> NewsSummaryResponse:
    """Summarize a batch of headlines and classify impact + affected currency."""
    return await ai.news_summary(body)
