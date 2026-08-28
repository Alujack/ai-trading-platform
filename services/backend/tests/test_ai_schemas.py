"""Pydantic schema validation tests for the analyze endpoints.

Carried over from `services/ai/tests/test_schemas.py` when that service was
folded into this backend (plan 11, Phase 3). The schemas define the wire contract
of `/analyze/*` and of the in-process `integrations.ai.client` calls, so their
validation rules are worth pinning here rather than losing with the old tree.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.integrations.ai.schemas import (
    JournalReviewRequest,
    JournalReviewResponse,
    MarketContextRequest,
    MarketContextResponse,
    ValidateSignalRequest,
    ValidateSignalResponse,
    serialize_for_prompt,
)


def _candle(ts: str = "2026-05-15T12:00:00Z") -> dict:
    return {"timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}


def _indicator(ts: str = "2026-05-15T12:00:00Z") -> dict:
    return {"timestamp": ts, "rsi": 55.2, "ema20": 100.0, "ema50": 99.5, "ema200": 98.0, "atr": 0.6}


def _signal() -> dict:
    return {
        "symbol": "XAUUSD",
        "timeframe": "60min",
        "direction": "LONG",
        "entryPrice": 2350.0,
        "stopLoss": 2342.0,
        "takeProfit": 2370.0,
        "confidenceScore": 0,
        "aiReasoning": "strategy ok",
    }


def _journal_trade() -> dict:
    return {
        "symbol": "EURUSD",
        "direction": "LONG",
        "entryPrice": 1.08,
        "exitPrice": 1.082,
        "profitLoss": 20.0,
        "openedAt": "2026-05-15T10:00:00Z",
        "closedAt": "2026-05-15T11:30:00Z",
        "notes": "Bought the EMA20 retest",
        "emotions": "patient",
    }


# ---- MarketContextRequest -----------------------------------------------------


class TestMarketContextRequest:
    def test_minimal_valid_body_parses(self):
        body = MarketContextRequest(
            symbol="XAUUSD",
            timeframe="60min",
            candles=[_candle()],
            indicators=[_indicator()],
            news=[],
        )
        assert body.symbol == "XAUUSD"
        assert len(body.candles) == 1

    def test_requires_candles(self):
        with pytest.raises(ValidationError):
            MarketContextRequest(symbol="XAUUSD", timeframe="60min", indicators=[], news=[])  # type: ignore[call-arg]

    def test_candles_extra_fields_allowed(self):
        body = MarketContextRequest(
            symbol="XAUUSD",
            timeframe="60min",
            candles=[{**_candle(), "extra_db_field": "ok"}],
            indicators=[_indicator()],
            news=[],
        )
        # extra_db_field should not raise; permissive nested model
        assert body.candles[0].timestamp == "2026-05-15T12:00:00Z"

    def test_indicator_fields_nullable(self):
        body = MarketContextRequest(
            symbol="XAUUSD",
            timeframe="60min",
            candles=[_candle()],
            indicators=[{"timestamp": "2026-05-15T12:00:00Z"}],
            news=[],
        )
        assert body.indicators[0].rsi is None
        assert body.indicators[0].ema20 is None


# ---- MarketContextResponse ----------------------------------------------------


class TestMarketContextResponse:
    def test_accepts_valid_bias(self):
        r = MarketContextResponse(
            bias="Bullish", summary="Trend up", keyLevels=["2350"], risks=["FOMC"]
        )
        assert r.bias == "Bullish"

    def test_rejects_unknown_bias(self):
        with pytest.raises(ValidationError):
            MarketContextResponse(bias="Choppy", summary="", keyLevels=[], risks=[])  # type: ignore[arg-type]

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            MarketContextResponse(
                bias="Neutral",
                summary="s",
                keyLevels=[],
                risks=[],
                hallucinated_field="oops",  # type: ignore[call-arg]
            )


# ---- ValidateSignalRequest ----------------------------------------------------


class TestValidateSignalRequest:
    def test_accepts_camelcase_upcomingNews_alias(self):
        body = ValidateSignalRequest.model_validate(
            {
                "signal": _signal(),
                "candles": [_candle()],
                "indicators": [_indicator()],
                "upcomingNews": [],
            }
        )
        assert body.signal.symbol == "XAUUSD"

    def test_accepts_snake_case_via_populate_by_name(self):
        body = ValidateSignalRequest.model_validate(
            {
                "signal": _signal(),
                "candles": [_candle()],
                "indicators": [_indicator()],
                "upcoming_news": [],
            }
        )
        assert body.upcoming_news == []

    def test_signal_required_camel_case_aliases(self):
        body = ValidateSignalRequest.model_validate(
            {
                "signal": _signal(),
                "candles": [_candle()],
                "indicators": [_indicator()],
                "upcomingNews": [],
            }
        )
        assert body.signal.entry_price == 2350.0
        assert body.signal.stop_loss == 2342.0
        assert body.signal.take_profit == 2370.0

    def test_signal_rejects_unknown_direction(self):
        bad = _signal() | {"direction": "SIDEWAYS"}
        with pytest.raises(ValidationError):
            ValidateSignalRequest.model_validate(
                {
                    "signal": bad,
                    "candles": [_candle()],
                    "indicators": [_indicator()],
                    "upcomingNews": [],
                }
            )


# ---- ValidateSignalResponse ---------------------------------------------------


class TestValidateSignalResponse:
    def test_accepts_score_in_range(self):
        r = ValidateSignalResponse(score=75, approved=True, reasoning="ok", concerns=[])
        assert r.score == 75

    def test_rejects_score_above_100(self):
        with pytest.raises(ValidationError):
            ValidateSignalResponse(score=101, approved=False, reasoning="", concerns=[])

    def test_rejects_score_below_zero(self):
        with pytest.raises(ValidationError):
            ValidateSignalResponse(score=-1, approved=False, reasoning="", concerns=[])

    def test_score_boundaries_inclusive(self):
        assert ValidateSignalResponse(score=0, approved=False, reasoning="", concerns=[]).score == 0
        assert (
            ValidateSignalResponse(score=100, approved=True, reasoning="", concerns=[]).score == 100
        )


# ---- JournalReviewRequest / Response ------------------------------------------


class TestJournalReview:
    def test_request_requires_at_least_one_trade(self):
        with pytest.raises(ValidationError):
            JournalReviewRequest(trades=[])

    def test_request_accepts_camelcase_trade_aliases(self):
        body = JournalReviewRequest.model_validate({"trades": [_journal_trade()]})
        assert body.trades[0].entry_price == 1.08
        assert body.trades[0].exit_price == 1.082
        assert body.trades[0].profit_loss == 20.0

    def test_response_fields_required(self):
        with pytest.raises(ValidationError):
            JournalReviewResponse(  # type: ignore[call-arg]
                patterns=["x"],
                strengths=["y"],
                weaknesses=["z"],
                # suggestions missing
            )


# ---- serialize_for_prompt -----------------------------------------------------


class TestSerializeForPrompt:
    def test_uses_camelcase_aliases(self):
        body = ValidateSignalRequest.model_validate(
            {
                "signal": _signal(),
                "candles": [_candle()],
                "indicators": [_indicator()],
                "upcomingNews": [],
            }
        )
        out = serialize_for_prompt(body)
        assert "upcomingNews" in out
        assert "entryPrice" in out["signal"]
        # snake_case must NOT leak
        assert "upcoming_news" not in out
        assert "entry_price" not in out["signal"]

    def test_excludes_nones(self):
        body = MarketContextRequest(
            symbol="XAUUSD",
            timeframe="60min",
            candles=[_candle()],
            indicators=[{"timestamp": "2026-05-15T12:00:00Z"}],
            news=[],
        )
        out = serialize_for_prompt(body)
        # The indicator with no values should be dumped with only timestamp
        assert out["indicators"][0] == {"timestamp": "2026-05-15T12:00:00Z"}
