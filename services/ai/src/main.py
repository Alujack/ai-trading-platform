"""FastAPI AI analysis service entry point."""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from .providers import (
    analyze,
    available,
    clear_key,
    get_active,
    provider_details,
    set_active,
    set_key,
    test_provider,
)
from .prompts import (
    JOURNAL_REVIEW_SYSTEM,
    MARKET_CONTEXT_SYSTEM,
    NEWS_SUMMARY_SYSTEM,
    TRADE_REVIEW_SYSTEM,
    VALIDATE_SIGNAL_SYSTEM,
)
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
from .settings import get_settings

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AI Trading Analysis Service", version="0.1.0")

analyze_router = APIRouter(prefix="/analyze", tags=["analyze"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class ProviderDetail(BaseModel):
    name: str
    label: str
    needsKey: bool
    hasKey: bool
    keyHint: str | None = None
    keySource: str | None = None
    model: str | None = None
    configured: bool
    active: bool


class ProviderState(BaseModel):
    active: str
    available: list[str]
    providers: list[ProviderDetail]


class SetProviderBody(BaseModel):
    provider: str


class SetKeyBody(BaseModel):
    provider: str
    apiKey: str
    model: str | None = None


class ProviderName(BaseModel):
    provider: str


class TestResult(BaseModel):
    ok: bool
    detail: str


def _state() -> ProviderState:
    cfg = get_settings()
    return ProviderState(
        active=get_active(cfg),
        available=available(cfg),
        providers=[ProviderDetail(**d) for d in provider_details(cfg)],
    )


@app.get("/provider", response_model=ProviderState)
def provider_get() -> ProviderState:
    """Current AI provider, selectable list, and per-provider config status."""
    return _state()


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
    return _state()


@app.put("/provider/key", response_model=ProviderState)
def provider_set_key(body: SetKeyBody) -> ProviderState:
    """Save an API key (and optional model) for a provider, pasted from the UI."""
    try:
        set_key(body.provider, body.apiKey, body.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"'{body.provider}' does not accept a key.") from exc
    return _state()


@app.delete("/provider/key", response_model=ProviderState)
def provider_clear_key(body: ProviderName) -> ProviderState:
    """Remove a UI-set key for a provider (env keys, if any, remain)."""
    clear_key(body.provider)
    return _state()


@app.post("/provider/test", response_model=TestResult)
def provider_test(body: ProviderName) -> TestResult:
    """Make one tiny real call to verify a provider's key works."""
    return TestResult(**test_provider(body.provider))
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


@analyze_router.post("/trade-review", response_model=TradeReviewResponse)
def trade_review(body: TradeReviewRequest) -> TradeReviewResponse:
    """Grade ONE closed trade on process (not outcome) and explain why it won/lost.

    Called when a trade closes (the learning loop): the verdict + lesson are
    stored on the Journal so the agent compounds discipline over time.
    """
    return analyze(
        system_prompt=TRADE_REVIEW_SYSTEM,
        user_payload=serialize_for_prompt(body),
        response_model=TradeReviewResponse,
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
