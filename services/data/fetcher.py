"""OHLCV fetcher with per-symbol provider routing.

XAUUSD goes through Twelve Data (Alpha Vantage's free tier does not support gold).
EURUSD and BTCUSD go through Alpha Vantage's free daily endpoints.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from db import CandleRow

# ---- Rate limiting --------------------------------------------------------
# Twelve Data's free tier allows ~8 requests/min. The worker runs several
# timeframe loops concurrently, so without spacing they burst past the limit and
# get 429'd (no data lands). This global throttle serializes Twelve Data calls
# and spaces them out so requests actually succeed. Tune via env.
_TD_MIN_INTERVAL_S = float(os.environ.get("TWELVEDATA_MIN_INTERVAL_S", "8"))
_td_lock = asyncio.Lock()
_td_last = 0.0


async def _td_throttle() -> None:
    global _td_last
    async with _td_lock:
        wait = _TD_MIN_INTERVAL_S - (time.monotonic() - _td_last)
        if wait > 0:
            await asyncio.sleep(wait)
        _td_last = time.monotonic()

# ---- Shared ---------------------------------------------------------------

INTRADAY_INTERVALS = {"1min", "5min", "15min", "60min"}
DAILY = "daily"

# Which provider handles each symbol.
PROVIDER_BY_SYMBOL: dict[str, str] = {
    "XAUUSD": "twelvedata",
    "EURUSD": "twelvedata",
    "BTCUSD": "twelvedata",
}

# For main.py to enumerate symbols.
SYMBOL_MAP: dict[str, str] = dict(PROVIDER_BY_SYMBOL)


async def fetch_candles(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    *,
    end_date: datetime | None = None,
    output_size: int = 100,
) -> list[CandleRow]:
    """Fetch a batch of OHLCV candles for symbol/timeframe.

    end_date (Twelve Data only) lets callers page backward through history —
    bars returned will have timestamps ≤ end_date. Used by backfill scripts.

    output_size (Twelve Data only) is the max bars per request. The live worker
    uses the small default; backfill passes the free-tier max (5000) to pull far
    more history per credit.
    """
    provider = PROVIDER_BY_SYMBOL.get(symbol)
    if provider == "twelvedata":
        return await _fetch_twelvedata(
            client, symbol, timeframe, end_date=end_date, output_size=output_size
        )
    if provider == "alpha_vantage":
        return await _fetch_alpha_vantage(client, symbol, timeframe)
    raise ValueError(f"No provider configured for symbol {symbol}")


# ---- Twelve Data ----------------------------------------------------------

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

# Twelve Data uses slash-separated pairs and different interval labels.
_TWELVEDATA_SYMBOL: dict[str, str] = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD",
    "BTCUSD": "BTC/USD",
}
_TWELVEDATA_INTERVAL: dict[str, str] = {
    "1min": "1min",
    "5min": "5min",
    "15min": "15min",
    "60min": "1h",
    "daily": "1day",
}


def _twelvedata_key() -> str:
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        raise RuntimeError("TWELVEDATA_API_KEY is not set in environment")
    return key


def _parse_twelvedata_timestamp(raw: str) -> datetime:
    if " " in raw:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return datetime.strptime(raw, "%Y-%m-%d")


async def _fetch_twelvedata(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    *,
    end_date: datetime | None = None,
    output_size: int = 100,
) -> list[CandleRow]:
    # Twelve Data caps outputsize at 5000 per request on every plan (incl. free).
    size = max(1, min(int(output_size), 5000))
    params: dict[str, str] = {
        "symbol": _TWELVEDATA_SYMBOL[symbol],
        "interval": _TWELVEDATA_INTERVAL[timeframe],
        "outputsize": str(size),
        "format": "JSON",
        "apikey": _twelvedata_key(),
    }
    if end_date is not None:
        params["end_date"] = end_date.strftime("%Y-%m-%d %H:%M:%S")
    await _td_throttle()
    resp = await client.get(TWELVEDATA_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()

    if payload.get("status") == "error":
        raise RuntimeError(f"Twelve Data error: {payload.get('message', payload)}")

    values: list[dict[str, str]] = payload.get("values") or []
    rows: list[CandleRow] = []
    for entry in values:
        ts = _parse_twelvedata_timestamp(entry["datetime"])
        # Volume may be missing or empty string for FX/metals on Twelve Data.
        raw_vol = entry.get("volume") or "0"
        rows.append(
            CandleRow(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=Decimal(entry["open"]),
                high=Decimal(entry["high"]),
                low=Decimal(entry["low"]),
                close=Decimal(entry["close"]),
                volume=Decimal(raw_vol),
            )
        )
    return rows


# ---- Alpha Vantage --------------------------------------------------------

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

# kind="fx" → FX_DAILY (free; intraday FX is premium)
# kind="crypto" → DIGITAL_CURRENCY_DAILY (free; crypto intraday is premium)
_ALPHA_VANTAGE_KIND: dict[str, tuple[str, str, str]] = {
    "EURUSD": ("fx", "EUR", "USD"),
    "BTCUSD": ("crypto", "BTC", "USD"),
}


def _alpha_vantage_key() -> str:
    key = os.environ.get("ALPHA_VANTAGE_KEY")
    if not key:
        raise RuntimeError("ALPHA_VANTAGE_KEY is not set in environment")
    return key


def _alpha_vantage_params(symbol: str, timeframe: str) -> dict[str, str]:
    kind, base, quote = _ALPHA_VANTAGE_KIND[symbol]
    key = _alpha_vantage_key()
    if kind == "fx":
        if timeframe == DAILY:
            return {
                "function": "FX_DAILY",
                "from_symbol": base,
                "to_symbol": quote,
                "outputsize": "compact",
                "apikey": key,
            }
        return {
            "function": "FX_INTRADAY",
            "from_symbol": base,
            "to_symbol": quote,
            "interval": timeframe,
            "outputsize": "compact",
            "apikey": key,
        }
    # crypto
    if timeframe == DAILY:
        return {
            "function": "DIGITAL_CURRENCY_DAILY",
            "symbol": base,
            "market": quote,
            "apikey": key,
        }
    return {
        "function": "CRYPTO_INTRADAY",
        "symbol": base,
        "market": quote,
        "interval": timeframe,
        "outputsize": "compact",
        "apikey": key,
    }


def _find_av_series_key(payload: dict[str, Any]) -> str:
    for k in payload:
        if k.startswith("Time Series") or "Digital Currency" in k:
            return k
    raise ValueError(f"No time series found in Alpha Vantage response. Keys={list(payload)}")


def _parse_av_timestamp(raw: str) -> datetime:
    if len(raw) == 10:
        return datetime.strptime(raw, "%Y-%m-%d")
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")


def _pick(entry: dict[str, str], *candidates: str) -> str:
    for c in candidates:
        if c in entry:
            return entry[c]
    raise KeyError(f"None of {candidates} found in entry keys={list(entry)}")


def _parse_av_rows(symbol: str, timeframe: str, payload: dict[str, Any]) -> list[CandleRow]:
    for problem_key in ("Error Message", "Note", "Information"):
        if problem_key in payload:
            raise RuntimeError(f"Alpha Vantage {problem_key}: {payload[problem_key]}")

    series_key = _find_av_series_key(payload)
    series: dict[str, dict[str, str]] = payload[series_key]
    rows: list[CandleRow] = []
    for raw_ts, entry in series.items():
        ts = _parse_av_timestamp(raw_ts)
        open_ = _pick(entry, "1. open", "1a. open (USD)")
        high_ = _pick(entry, "2. high", "2a. high (USD)")
        low_ = _pick(entry, "3. low", "3a. low (USD)")
        close_ = _pick(entry, "4. close", "4a. close (USD)")
        try:
            volume = Decimal(_pick(entry, "5. volume"))
        except KeyError:
            volume = Decimal("0")
        rows.append(
            CandleRow(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=Decimal(open_),
                high=Decimal(high_),
                low=Decimal(low_),
                close=Decimal(close_),
                volume=volume,
            )
        )
    return rows


async def _fetch_alpha_vantage(
    client: httpx.AsyncClient, symbol: str, timeframe: str
) -> list[CandleRow]:
    params = _alpha_vantage_params(symbol, timeframe)
    resp = await client.get(ALPHA_VANTAGE_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    return _parse_av_rows(symbol, timeframe, resp.json())
