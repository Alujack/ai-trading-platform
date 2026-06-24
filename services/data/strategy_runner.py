"""Strategy runner — the single driver that turns enabled strategies into signals.

Each tick it: reads the enabled rows from the `Strategy` table, builds a
`BarWindow` per (symbol, timeframe), runs every strategy's `evaluate`, and POSTs
each `SignalCandidate` to the API gate (`POST /api/signals/candidate`). The gate
runs AI validation + the risk engine and persists tagged PENDING signals — this
runner never writes to the Signal table directly.
"""
from __future__ import annotations

import json
import logging
import os

import asyncpg
import httpx

from db import init_pool
from fetcher import SYMBOL_MAP
from indicator_calculator import normalize_timeframe
from regime import (
    REGIME_LOOKBACK_BARS,
    UNKNOWN,
    RegimeReading,
    compute_regime,
    gating_enabled,
)
from strategies import BarWindow, IndicatorBar, build_strategy

log = logging.getLogger("data.strategy_runner")

RUNNER_LOOKBACK_BARS = 5


def _gate_url() -> str:
    base = os.environ.get("API_PUBLIC_URL", "http://localhost:4000").rstrip("/")
    return os.environ.get("STRATEGY_GATE_URL") or f"{base}/api/signals/candidate"


def _timeframes() -> list[str]:
    raw = os.environ.get("STRATEGY_TIMEFRAMES", "15min,60min")
    return [normalize_timeframe(t.strip()) for t in raw.split(",") if t.strip()]


def _symbols() -> list[str]:
    """Which symbols to scan, in SYMBOL_MAP order.

    Defaults to every known symbol. Set STRATEGY_SYMBOLS (comma-separated) to
    trade only a subset — e.g. STRATEGY_SYMBOLS="XAUUSD,EURUSD" skips BTCUSD.
    Unknown names are dropped with a warning so a typo can't silently widen or
    empty the universe.
    """
    known = list(SYMBOL_MAP.keys())
    raw = os.environ.get("STRATEGY_SYMBOLS")
    if not raw or not raw.strip():
        return known
    requested = [s.strip().upper() for s in raw.split(",") if s.strip()]
    selected, unknown = [], []
    for sym in requested:
        if sym in SYMBOL_MAP and sym not in selected:
            selected.append(sym)
        elif sym not in SYMBOL_MAP:
            unknown.append(sym)
    if unknown:
        log.warning("strategy_symbols_unknown ignored=%s known=%s", unknown, known)
    if not selected:
        # Fail closed: the operator asked to restrict but nothing matched. Scanning
        # nothing is safer than silently trading symbols they tried to exclude.
        log.error("strategy_symbols_none_valid raw=%r scanning_no_symbols", raw)
    return selected


def _as_params(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        return json.loads(raw)
    return {}


async def _load_enabled_strategies(pool: asyncpg.Pool) -> list[tuple[str, dict]]:
    rows = await pool.fetch('SELECT "name", "params" FROM "Strategy" WHERE "enabled" = true')
    return [(r["name"], _as_params(r["params"])) for r in rows]


async def _load_window(
    pool: asyncpg.Pool, symbol: str, timeframe: str, limit: int
) -> BarWindow:
    rows = await pool.fetch(
        """
        SELECT c."timestamp" AS bar_ts,
               c."open" AS open, c."high" AS high, c."low" AS low, c."close" AS close,
               i."rsi" AS rsi, i."ema20" AS ema20, i."ema50" AS ema50,
               i."ema200" AS ema200, i."atr" AS atr
        FROM "Candle" c
        JOIN "Indicator" i
          ON i."symbol" = c."symbol"
         AND i."timeframe" = c."timeframe"
         AND i."timestamp" = c."timestamp"
        WHERE c."symbol" = $1 AND c."timeframe" = $2
        ORDER BY c."timestamp" DESC
        LIMIT $3
        """,
        symbol,
        timeframe,
        limit,
    )
    bars = [
        IndicatorBar(
            timestamp=r["bar_ts"],
            close=r["close"],
            rsi=r["rsi"],
            ema20=r["ema20"],
            ema50=r["ema50"],
            ema200=r["ema200"],
            atr=r["atr"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
        )
        for r in rows
    ]
    return BarWindow(symbol=symbol, timeframe=timeframe, bars=bars)


async def _load_regime(pool: asyncpg.Pool, symbol: str, timeframe: str) -> RegimeReading:
    """Classify the current regime for (symbol, timeframe) from raw candles.

    Uses high/low/close directly (ADX needs the range), independent of the
    persisted Indicator rows, so the regime gate doesn't depend on the indicator
    pipeline having run for this exact bar.
    """
    rows = await pool.fetch(
        """
        SELECT "high", "low", "close"
        FROM "Candle"
        WHERE "symbol" = $1 AND "timeframe" = $2
        ORDER BY "timestamp" DESC
        LIMIT $3
        """,
        symbol,
        timeframe,
        REGIME_LOOKBACK_BARS,
    )
    if not rows:
        return RegimeReading(UNKNOWN, None, None, "no candles")
    # Query is most-recent first; compute_regime wants oldest-first.
    rows = list(reversed(rows))
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    closes = [float(r["close"]) for r in rows]
    return compute_regime(highs, lows, closes)


async def _post_candidate(client: httpx.AsyncClient, gate_url: str, payload: dict) -> str:
    try:
        resp = await client.post(gate_url, json=payload, timeout=90.0)
    except Exception as exc:  # noqa: BLE001 — gate unreachable; report and move on
        return f"gate_unreachable: {exc}"
    if resp.status_code not in (200, 201):
        return f"gate_http_{resp.status_code}: {resp.text[:120]}"
    body = resp.json()
    status = body.get("status", "?")
    extra = body.get("signalId") or body.get("reason") or ""
    return f"{status} {extra}".strip()


async def run_once(client: httpx.AsyncClient) -> int:
    """One scan across all enabled strategies × symbols × timeframes."""
    pool = await init_pool()
    gate_url = _gate_url()
    strategies = await _load_enabled_strategies(pool)
    if not strategies:
        log.info("strategy_scan no_enabled_strategies")
        return 0

    timeframes = _timeframes()
    symbols = _symbols()
    gating = gating_enabled()
    # Regime depends only on (symbol, timeframe), so classify each once per scan
    # and reuse across every strategy.
    regime_cache: dict[tuple[str, str], RegimeReading] = {}

    async def _regime(symbol: str, timeframe: str) -> RegimeReading:
        key = (symbol, timeframe)
        if key not in regime_cache:
            regime_cache[key] = await _load_regime(pool, symbol, timeframe)
        return regime_cache[key]

    generated = 0
    for name, params in strategies:
        try:
            strategy = build_strategy(name, params)
        except KeyError as exc:
            log.error("strategy_unknown name=%s err=%s", name, exc)
            continue
        # Multi-bar price-action detectors (ICT) declare how much trailing OHLC
        # history they need; close-only strategies omit it and use a small window.
        bars_needed = max(RUNNER_LOOKBACK_BARS, int(getattr(strategy, "lookback", 1)))
        for symbol in symbols:
            for timeframe in timeframes:
                window = await _load_window(pool, symbol, timeframe, bars_needed)
                if not window.bars:
                    continue
                if gating:
                    reading = await _regime(symbol, timeframe)
                    # UNKNOWN fails open (don't halt trading on thin data); a known
                    # regime the strategy doesn't trade gates it out for this bar.
                    if reading.regime != UNKNOWN and reading.regime not in strategy.regimes:
                        log.info(
                            "candidate_gated strategy=%s symbol=%s tf=%s regime=%s "
                            "allowed=%s reason=%s",
                            name,
                            symbol,
                            timeframe,
                            reading.regime,
                            sorted(strategy.regimes),
                            reading.reason,
                        )
                        continue
                candidates = strategy.evaluate(window)
                for cand in candidates:
                    outcome = await _post_candidate(client, gate_url, cand.to_payload())
                    if outcome.startswith("generated"):
                        generated += 1
                    log.info(
                        "candidate strategy=%s symbol=%s tf=%s dir=%s -> %s",
                        name,
                        symbol,
                        timeframe,
                        cand.direction,
                        outcome,
                    )
    log.info("strategy_scan_done strategies=%d generated=%d", len(strategies), generated)
    return generated
