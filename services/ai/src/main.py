"""FastAPI AI analysis service entry point."""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from .providers import analyze, available, get_active, set_active
from .prompts import (
    JOURNAL_REVIEW_SYSTEM,
    MARKET_CONTEXT_SYSTEM,
    NEWS_SUMMARY_SYSTEM,
    VALIDATE_SIGNAL_SYSTEM,
)
from .schemas import (
    JournalReviewRequest,
    JournalReviewResponse,
    MarketContextRequest,
    MarketContextResponse,
    NewsSummaryRequest,
    NewsSummaryResponse,
    ValidateSignalRequest,
    ValidateSignalResponse,
    serialize_for_prompt,
)
from .settings import get_settings

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AI Trading Analysis Service", version="0.1.0")

analyze_router = APIRouter(prefix="/analyze", tags=["analyze"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class ProviderState(BaseModel):
    active: str
    available: list[str]


class SetProviderBody(BaseModel):
    provider: str


@app.get("/provider", response_model=ProviderState)
def provider_get() -> ProviderState:
    """Current AI provider + the ones selectable given configured keys."""
    cfg = get_settings()
    return ProviderState(active=get_active(cfg), available=available(cfg))


@app.post("/provider", response_model=ProviderState)
def provider_set(body: SetProviderBody) -> ProviderState:
    """Switch the active provider at runtime (mock / anthropic / gemini)."""
    cfg = get_settings()
    try:
        set_active(body.provider, cfg)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{body.provider}' is not available. Choose one of {available(cfg)}.",
        ) from exc
    return ProviderState(active=get_active(cfg), available=available(cfg))


@analyze_router.post("/market-context", response_model=MarketContextResponse)
def market_context(body: MarketContextRequest) -> MarketContextResponse:
    """Produce a structured market-context briefing from price action + news."""
    return analyze(
        system_prompt=MARKET_CONTEXT_SYSTEM,
        user_payload=serialize_for_prompt(body),
        response_model=MarketContextResponse,
    )


@analyze_router.post("/validate-signal", response_model=ValidateSignalResponse)
def validate_signal(body: ValidateSignalRequest) -> ValidateSignalResponse:
    """Score and approve/reject a proposed trade signal against market context."""
    return analyze(
        system_prompt=VALIDATE_SIGNAL_SYSTEM,
        user_payload=serialize_for_prompt(body),
        response_model=ValidateSignalResponse,
    )


@analyze_router.post("/journal-review", response_model=JournalReviewResponse)
def journal_review(body: JournalReviewRequest) -> JournalReviewResponse:
    """Extract behavioral patterns and actionable critique from recent trades."""
    return analyze(
        system_prompt=JOURNAL_REVIEW_SYSTEM,
        user_payload=serialize_for_prompt(body),
        response_model=JournalReviewResponse,
    )


@analyze_router.post("/news-summary", response_model=NewsSummaryResponse)
def news_summary(body: NewsSummaryRequest) -> NewsSummaryResponse:
    """Summarize a batch of headlines and classify impact + affected currency.

    Called by the n8n breaking-news workflow. Routes through analyze(), so it
    honors the active Mock/Claude/Gemini provider set via /provider.
    """
    return analyze(
        system_prompt=NEWS_SUMMARY_SYSTEM,
        user_payload=serialize_for_prompt(body),
        response_model=NewsSummaryResponse,
    )


app.include_router(analyze_router)
