"""FastAPI AI analysis service entry point."""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI

from .llm import analyze
from .prompts import (
    JOURNAL_REVIEW_SYSTEM,
    MARKET_CONTEXT_SYSTEM,
    VALIDATE_SIGNAL_SYSTEM,
)
from .schemas import (
    JournalReviewRequest,
    JournalReviewResponse,
    MarketContextRequest,
    MarketContextResponse,
    ValidateSignalRequest,
    ValidateSignalResponse,
    serialize_for_prompt,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AI Trading Analysis Service", version="0.1.0")

analyze_router = APIRouter(prefix="/analyze", tags=["analyze"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


app.include_router(analyze_router)
