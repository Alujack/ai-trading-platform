"""OHLCV fetcher with per-symbol provider routing.

CANDLE_SOURCE=mt5 routes live fetches through the local MT5 bridge (broker-
accurate OHLCV straight from the trading account, UTC timestamps, no rate
limits), falling back to the HTTP providers below when the bridge/terminal is
down. Otherwise: XAUUSD/EURUSD via Twelve Data and BTCUSD via Kraken Spot;
Alpha Vantage remains as a secondary provider option.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, time as dt_time, timezone
from decimal import Decimal
from typing import Any

import httpx

from db import CandleRow

log = logging.getLogger("data.fetcher")

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
    # Public, keyless, genuinely 24/7 BTC/USD venue. Keeping this off the
    # TwelveData quota lets crypto stay live without starving gold.
    "BTCUSD": "kraken",
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

    end_date (provider permitting) lets callers page backward through history —
    bars returned will have timestamps ≤ end_date. Used by backfill scripts.

    output_size is the requested max bars. Providers clamp it to their own cap.
    """
    # Broker feed first when enabled. end_date paging isn't supported by the
    # bridge, so backfill scripts always take the HTTP-provider path.
    if _mt5_enabled() and end_date is None:
        try:
            return await _fetch_mt5(client, symbol, timeframe, count=output_size)
        except Exception as exc:  # noqa: BLE001 — bridge/terminal down; use fallback
            log.warning("mt5_fetch_failed symbol=%s tf=%s err=%s — falling back", symbol, timeframe, exc)

    provider = PROVIDER_BY_SYMBOL.get(symbol)
    if provider == "twelvedata":
        return await _fetch_twelvedata(
            client, symbol, timeframe, end_date=end_date, output_size=output_size
        )
    if provider == "kraken":
        return await _fetch_kraken(
            client, symbol, timeframe, end_date=end_date, output_size=output_size
        )
    if provider == "alpha_vantage":
        return await _fetch_alpha_vantage(client, symbol, timeframe)
    raise ValueError(f"No provider configured for symbol {symbol}")


# ---- Kraken Spot ----------------------------------------------------------

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
_KRAKEN_PAIR: dict[str, str] = {"BTCUSD": "BTC/USD"}
_KRAKEN_INTERVAL: dict[str, int] = {
    "1min": 1,
    "5min": 5,
    "15min": 15,
    "60min": 60,
    "daily": 1440,
}


async def _fetch_kraken(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    *,
    end_date: datetime | None = None,
    output_size: int = 100,
) -> list[CandleRow]:
    """Fetch recent keyless BTC/USD candles from Kraken Spot.

    Kraken returns at most 720 recent bars and always includes the current,
    not-yet-committed candle. ``since`` is incremental rather than a deep-history
    cursor, so this provider does not pretend it can page beyond that window.
    """
    pair = _KRAKEN_PAIR[symbol]
    interval = _KRAKEN_INTERVAL[timeframe]
    size = max(1, min(int(output_size), 720))
    params: dict[str, str] = {
        "pair": pair,
        "interval": str(interval),
        "assetVersion": "1",
    }
    if end_date is not None:
        params["since"] = str(max(0, int(end_date.timestamp()) - interval * 60 * size))

    resp = await client.get(KRAKEN_OHLC_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    errors = payload.get("error") or []
    if errors:
        raise RuntimeError(f"Kraken error: {', '.join(str(e) for e in errors)}")
    result = payload.get("result") or {}
    values = result.get(pair) or []
    rows: list[CandleRow] = []
    for entry in values[-size:]:
        if not isinstance(entry, list) or len(entry) < 8:
            continue
        rows.append(
            CandleRow(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(int(entry[0]), tz=timezone.utc).replace(tzinfo=None),
                open=Decimal(str(entry[1])),
                high=Decimal(str(entry[2])),
                low=Decimal(str(entry[3])),
                close=Decimal(str(entry[4])),
                volume=Decimal(str(entry[6])),
            )
        )
    return rows


# ---- MT5 bridge (broker feed) ----------------------------------------------

def _mt5_enabled() -> bool:
    return os.environ.get("CANDLE_SOURCE", "").strip().lower() == "mt5"


def _mt5_symbol(symbol: str) -> str:
    """Platform symbol → broker Market Watch name (e.g. XAUUSD → XAUUSDm).

    Shares BROKER_SYMBOL_MAP with the API's execution layer so data and orders
    can never disagree on the broker symbol.
    """
    raw = os.environ.get("BROKER_SYMBOL_MAP", "")
    if raw:
        try:
            mapped = json.loads(raw).get(symbol)
            if isinstance(mapped, str) and mapped:
                return mapped
        except (ValueError, AttributeError):
            log.warning("broker_symbol_map_invalid raw=%r", raw)
    return symbol


async def _fetch_mt5(
    client: httpx.AsyncClient, symbol: str, timeframe: str, *, count: int = 100
) -> list[CandleRow]:
    base = os.environ.get("MT5_BRIDGE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("MT5_BRIDGE_URL is not set in environment")
    resp = await client.get(
        f"{base}/candles/{_mt5_symbol(symbol)}",
        params={"timeframe": timeframe, "count": max(1, min(int(count), 5000))},
        headers={"X-Bridge-Token": os.environ.get("MT5_BRIDGE_TOKEN", "")},
        timeout=30.0,
    )
    resp.raise_for_status()
    rows: list[CandleRow] = []
    for entry in resp.json():
        # Bridge timestamps are epoch seconds in UTC (Exness server = UTC+0);
        # store naive UTC to match the (post-shift) TwelveData series.
        ts = datetime.fromtimestamp(int(entry["timestamp"]), tz=timezone.utc).replace(tzinfo=None)
        rows.append(
            CandleRow(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=Decimal(str(entry["open"])),
                high=Decimal(str(entry["high"])),
                low=Decimal(str(entry["low"])),
                close=Decimal(str(entry["close"])),
                volume=Decimal(str(entry.get("volume") or 0)),
            )
        )
    return rows


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


# FX/metals venues are closed Fri 22:00 → Sun 22:00 UTC, but TwelveData serves
# flat weekend quote bars in that window anyway (even with timezone=UTC pinned).
# Storing them corrupts session/killzone logic and inflates bar counts, so they
# are dropped at the source. Crypto trades through the weekend and is exempt.
_WEEKEND_OPEN_SYMBOLS = {"BTCUSD"}


def _in_weekend_gap(ts: datetime) -> bool:
    wd = ts.weekday()  # Mon=0 … Sun=6
    if wd == 4:  # Friday
        return ts.time() >= dt_time(22, 0)
    if wd == 5:  # Saturday
        return True
    if wd == 6:  # Sunday
        return ts.time() < dt_time(22, 0)
    return False


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
        # Everything is stored as naive UTC. Without this param TwelveData
        # returns the instrument's "exchange" timezone (UTC+10 for XAU/EUR) —
        # the pre-2026-07-08 rows were stored that way and were shifted -10h in
        # a one-off migration when the MT5 bridge became the primary source.
        "timezone": "UTC",
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
    drop_weekend = symbol not in _WEEKEND_OPEN_SYMBOLS
    rows: list[CandleRow] = []
    for entry in values:
        ts = _parse_twelvedata_timestamp(entry["datetime"])
        if drop_weekend and _in_weekend_gap(ts):
            continue
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
