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
from strategies import BarWindow, IndicatorBar, build_strategy

log = logging.getLogger("data.strategy_runner")

SYMBOLS = list(SYMBOL_MAP.keys())
RUNNER_LOOKBACK_BARS = 5


def _gate_url() -> str:
    base = os.environ.get("API_PUBLIC_URL", "http://localhost:4000").rstrip("/")
    return os.environ.get("STRATEGY_GATE_URL") or f"{base}/api/signals/candidate"


def _timeframes() -> list[str]:
    raw = os.environ.get("STRATEGY_TIMEFRAMES", "15min,60min")
    return [normalize_timeframe(t.strip()) for t in raw.split(",") if t.strip()]


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
        SELECT c."timestamp" AS bar_ts, c."close" AS close,
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
        )
        for r in rows
    ]
    return BarWindow(symbol=symbol, timeframe=timeframe, bars=bars)


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
    generated = 0
    for name, params in strategies:
        try:
            strategy = build_strategy(name, params)
        except KeyError as exc:
            log.error("strategy_unknown name=%s err=%s", name, exc)
            continue
        for symbol in SYMBOLS:
            for timeframe in timeframes:
                window = await _load_window(pool, symbol, timeframe, RUNNER_LOOKBACK_BARS)
                if not window.bars:
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
