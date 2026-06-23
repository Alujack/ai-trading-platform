"""Economic-calendar and news ingestion into the NewsEvent table.

Two sources, intentionally complementary:

* ForexFactory weekly calendar (no auth) — the high-impact *scheduled* events the
  risk engine's news-blackout check cares about (NFP, CPI, FOMC, …).
* Alpha Vantage NEWS_SENTIMENT — general published headlines that feed the AI
  market-context / signal-validation prompts.

Both normalize to db.NewsEventRow and upsert on (title, scheduledAt).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from db import NewsEventRow, upsert_news_events

log = logging.getLogger("data.news")

# ---- ForexFactory weekly calendar -----------------------------------------

FOREXFACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_FF_IMPACT_MAP = {
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}


def _normalize_ff_impact(raw: str | None) -> str | None:
    """Map ForexFactory impact labels to our Impact enum, or None to skip.

    Non-economic rows (`Holiday`, blank) carry no tradable impact — drop them.
    """
    if not raw:
        return None
    return _FF_IMPACT_MAP.get(raw.strip().lower())


def _parse_ff_date(raw: str) -> datetime | None:
    """ForexFactory dates are ISO-8601 with offset, e.g. 2024-01-05T08:30:00-05:00."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt
    # Store naive UTC to match the schema's TIMESTAMP WITHOUT TIME ZONE columns.
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_ff_events(payload: Any) -> list[NewsEventRow]:
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected ForexFactory payload type: {type(payload).__name__}")
    rows: list[NewsEventRow] = []
    for entry in payload:
        impact = _normalize_ff_impact(entry.get("impact"))
        if impact is None:
            continue
        scheduled = _parse_ff_date(entry.get("date", ""))
        title = (entry.get("title") or "").strip()
        if scheduled is None or not title:
            continue
        rows.append(
            NewsEventRow(
                title=title,
                impact=impact,
                currency=(entry.get("country") or "").strip().upper() or "UNKNOWN",
                scheduled_at=scheduled,
                forecast=(entry.get("forecast") or None),
                previous=(entry.get("previous") or None),
                actual=(entry.get("actual") or None),
            )
        )
    return rows


async def fetch_forexfactory_week(client: httpx.AsyncClient) -> list[NewsEventRow]:
    resp = await client.get(FOREXFACTORY_URL, timeout=30.0)
    resp.raise_for_status()
    return _parse_ff_events(resp.json())


# ---- Alpha Vantage NEWS_SENTIMENT -----------------------------------------

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

# Published headlines are not scheduled events; treat them as low-impact context.
_AV_NEWS_IMPACT = "LOW"

# Tickers we ask Alpha Vantage about, mapped to the currency we record.
_AV_TICKER_CURRENCY: dict[str, str] = {
    "FOREX:USD": "USD",
    "FOREX:EUR": "EUR",
    "CRYPTO:BTC": "BTC",
}


def _alpha_vantage_key() -> str | None:
    return os.environ.get("ALPHA_VANTAGE_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")


def _parse_av_time(raw: str) -> datetime | None:
    """Alpha Vantage time_published format: 20240105T123000 (UTC).

    Returned naive (UTC), matching the schema's TIMESTAMP WITHOUT TIME ZONE columns.
    """
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%S")
    except (ValueError, TypeError):
        return None


def _currency_for_article(article: dict[str, Any], fallback: str) -> str:
    """Pick the currency from the article's strongest ticker_sentiment match."""
    for ts in article.get("ticker_sentiment", []) or []:
        ticker = ts.get("ticker", "")
        if ticker in _AV_TICKER_CURRENCY:
            return _AV_TICKER_CURRENCY[ticker]
    return fallback


def _parse_av_news(payload: dict[str, Any]) -> list[NewsEventRow]:
    for problem_key in ("Error Message", "Note", "Information"):
        if problem_key in payload:
            raise RuntimeError(f"Alpha Vantage {problem_key}: {payload[problem_key]}")

    feed = payload.get("feed") or []
    rows: list[NewsEventRow] = []
    for article in feed:
        title = (article.get("title") or "").strip()
        scheduled = _parse_av_time(article.get("time_published", ""))
        if not title or scheduled is None:
            continue
        rows.append(
            NewsEventRow(
                title=title[:300],
                impact=_AV_NEWS_IMPACT,
                currency=_currency_for_article(article, fallback="USD"),
                scheduled_at=scheduled,
            )
        )
    return rows


async def fetch_av_news(
    client: httpx.AsyncClient, tickers: Iterable[str] | None = None
) -> list[NewsEventRow]:
    key = _alpha_vantage_key()
    if not key:
        log.warning("av_news_skipped reason=no_api_key")
        return []
    ticker_list = list(tickers) if tickers is not None else list(_AV_TICKER_CURRENCY)
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ",".join(ticker_list),
        "sort": "LATEST",
        "limit": "50",
        "apikey": key,
    }
    resp = await client.get(ALPHA_VANTAGE_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    return _parse_av_news(resp.json())


# ---- Orchestration --------------------------------------------------------


async def ingest_forexfactory(client: httpx.AsyncClient) -> int:
    rows = await fetch_forexfactory_week(client)
    written = await upsert_news_events(rows)
    log.info("forexfactory_ingested events=%d", written)
    return written


async def ingest_alpha_vantage_news(client: httpx.AsyncClient) -> int:
    rows = await fetch_av_news(client)
    written = await upsert_news_events(rows)
    log.info("av_news_ingested events=%d", written)
    return written
