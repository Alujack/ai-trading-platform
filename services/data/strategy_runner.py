"""Strategy runner — the single driver that turns enabled strategies into signals.

Each tick it: reads the enabled rows from the `Strategy` table, builds a
`BarWindow` per (symbol, timeframe), runs every strategy's `evaluate`, and POSTs
each `SignalCandidate` to the API gate (`POST /api/signals/candidate`). The gate
runs AI validation + the risk engine and persists tagged PENDING signals — this
runner never writes to the Signal table directly.

Two guards live HERE rather than in the gate: the freshness check below (never
evaluate a stale series) and the regime gate. Both are bypassable for VISIBILITY
only, via the raw-feed flag (see `_raw_feed_enabled`): the candidate is still
posted, but tagged `preGatedBy` so the gate records it and refuses it without
running AI/risk — it can never become a Signal. With the flag off, both guards
skip the series outright exactly as before.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

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

# Freshness guard: never evaluate strategies against a stale series. A signal
# computed on old bars carries an entry/SL/TP that no longer reflects the
# market — the July 2026 outage went unnoticed for weeks precisely because the
# runner kept scanning frozen data without complaint. Allow 2× the timeframe
# (ingestion lag + a missed fetch); daily gets 3 days so a weekend doesn't
# false-alarm it.
_STALE_AGE_LIMIT_S: dict[str, float] = {
    "1min": 2 * 60,
    "5min": 2 * 5 * 60,
    "15min": 2 * 15 * 60,
    "60min": 2 * 60 * 60,
    "daily": 3 * 86_400,
}


def stale_age_limit_s(timeframe: str) -> float:
    return _STALE_AGE_LIMIT_S.get(timeframe, 2 * 60 * 60)


def _utcnow_naive() -> datetime:
    """Naive UTC now, matching the Candle table's naive-UTC timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _gate_url() -> str:
    base = os.environ.get("API_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    return os.environ.get("STRATEGY_GATE_URL") or f"{base}/api/signals/candidate"


def _raw_feed_url() -> str:
    base = os.environ.get("API_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/api/config/raw-feed"


def _layers_url() -> str:
    base = os.environ.get("API_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/api/config/layers"


def _raw_feed_env_default() -> bool:
    return os.environ.get("RAW_SIGNAL_FEED", "").strip().lower() in {"1", "true", "yes", "on"}


async def _raw_feed_enabled(client: httpx.AsyncClient) -> bool:
    """Is the raw ("layers off") feed on? Asked once per scan.

    When on, a candidate the REGIME gate would have dropped is still evaluated and
    posted — tagged `preGatedBy="regime"` — so the operator can see the pure
    strategy signal for manual trading. The API records it and refuses it without
    running AI/risk, so it can never become a Signal or a position.

    A probe failure falls back to RAW_SIGNAL_FEED (default off): the raw feed can
    only ever be turned off by an outage, never on.
    """
    try:
        resp = await client.get(_raw_feed_url(), timeout=5.0)
        resp.raise_for_status()
        return bool(resp.json().get("enabled", False))
    except Exception as exc:  # noqa: BLE001 — visibility feature; never break a scan
        log.debug("raw_feed_probe_failed err=%s falling_back_to_env", exc)
        return _raw_feed_env_default()


@dataclass(slots=True)
class LayerConfig:
    """Which discretionary layers the operator has switched off this scan.

    `param_overrides` are strategy-constructor params forced to False, and the
    mapping comes from the API rather than being restated here — the backend's
    `config.flags.LAYERS` registry stays the single source of truth for it.
    """

    regime_gating: bool = True
    param_overrides: dict[str, bool] = field(default_factory=dict)

    @property
    def all_on(self) -> bool:
        return self.regime_gating and not self.param_overrides


async def _layer_config(client: httpx.AsyncClient) -> LayerConfig:
    """Ask the API which layers are on. Asked once per scan.

    Fails SAFE: any probe failure leaves every layer in place, so a backend blip
    can only ever make the runner more selective, never less.
    """
    try:
        resp = await client.get(_layers_url(), timeout=5.0)
        resp.raise_for_status()
        layers = resp.json().get("layers", [])
    except Exception as exc:  # noqa: BLE001 — a probe failure must not skip a scan
        log.debug("layers_probe_failed err=%s keeping_all_layers", exc)
        return LayerConfig()

    cfg = LayerConfig()
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("enabled", True):
            continue
        if layer.get("key") == "regime_gating":
            cfg.regime_gating = False
        param = layer.get("param")
        if isinstance(param, str) and param:
            cfg.param_overrides[param] = False
    if not cfg.all_on:
        log.info(
            "layers_off regime_gating=%s params=%s",
            cfg.regime_gating,
            sorted(cfg.param_overrides),
        )
    return cfg


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
               c."volume" AS volume,
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
            volume=r["volume"],
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
    layers = await _layer_config(client)
    # Env var and operator switch both have to allow it; either one can gate off.
    gating = gating_enabled() and layers.regime_gating
    raw_feed = await _raw_feed_enabled(client)
    # Regime depends only on (symbol, timeframe), so classify each once per scan
    # and reuse across every strategy.
    regime_cache: dict[tuple[str, str], RegimeReading] = {}

    async def _regime(symbol: str, timeframe: str) -> RegimeReading:
        key = (symbol, timeframe)
        if key not in regime_cache:
            regime_cache[key] = await _load_regime(pool, symbol, timeframe)
        return regime_cache[key]

    generated = 0
    # One staleness verdict per (symbol, timeframe) per scan, so the skip is
    # logged once instead of once per strategy. The value is None when fresh, or a
    # human description of the staleness ("newest bar 72h old") that rides along to
    # the raw feed so the operator sees HOW stale the prices are.
    stale_cache: dict[tuple[str, str], str | None] = {}

    async def _series_staleness(symbol: str, timeframe: str) -> str | None:
        key = (symbol, timeframe)
        if key not in stale_cache:
            newest = await pool.fetchval(
                'SELECT MAX("timestamp") FROM "Candle" WHERE "symbol" = $1 AND "timeframe" = $2',
                symbol,
                timeframe,
            )
            if newest is None:
                stale_cache[key] = "no candles at all"
                log.warning("series_stale symbol=%s tf=%s reason=no_candles", symbol, timeframe)
            else:
                age_s = (_utcnow_naive() - newest).total_seconds()
                limit_s = stale_age_limit_s(timeframe)
                if age_s > limit_s:
                    age_min = age_s / 60
                    age = f"{age_min / 60:.0f}h" if age_min >= 120 else f"{age_min:.0f}m"
                    stale_cache[key] = f"newest bar {age} old (limit {limit_s / 60:.0f}m)"
                    log.warning(
                        "series_stale symbol=%s tf=%s newest=%s age_s=%.0f limit_s=%.0f",
                        symbol,
                        timeframe,
                        newest,
                        age_s,
                        limit_s,
                    )
                else:
                    stale_cache[key] = None
        return stale_cache[key]

    for name, params in strategies:
        try:
            # Layer switches override the stored params, so turning a filter off
            # applies to every strategy without editing any Strategy row.
            strategy = build_strategy(name, {**params, **layers.param_overrides})
        except KeyError as exc:
            log.error("strategy_unknown name=%s err=%s", name, exc)
            continue
        # Multi-bar price-action detectors (ICT) declare how much trailing OHLC
        # history they need; close-only strategies omit it and use a small window.
        bars_needed = max(RUNNER_LOOKBACK_BARS, int(getattr(strategy, "lookback", 1)))
        # Optional per-strategy scoping via Strategy table params: a strategy only
        # validated on certain symbols/timeframes restricts itself with e.g.
        # {"symbols": ["XAUUSD"], "timeframes": ["15min"]}. Empty/absent = no
        # restriction (today's behaviour). Strategy __init__s ignore unknown keys.
        only_symbols = {
            s.strip().upper() for s in params.get("symbols", []) if isinstance(s, str) and s.strip()
        }
        only_timeframes = {
            normalize_timeframe(t.strip())
            for t in params.get("timeframes", [])
            if isinstance(t, str) and t.strip()
        }
        for symbol in symbols:
            if only_symbols and symbol not in only_symbols:
                continue
            for timeframe in timeframes:
                if only_timeframes and timeframe not in only_timeframes:
                    continue
                # Stale series. With the raw feed off this is an absolute skip. With
                # it on the candidate is still evaluated and posted for VISIBILITY,
                # tagged with how stale the prices are — the gate refuses it without
                # AI/risk, so a stale-priced candidate can never become a Signal or
                # reach the broker. Read the tag before trading one by hand.
                stale_detail = await _series_staleness(symbol, timeframe)
                if stale_detail and not raw_feed:
                    continue
                window = await _load_window(pool, symbol, timeframe, bars_needed)
                if not window.bars:
                    continue
                regime_gated = False
                if gating:
                    reading = await _regime(symbol, timeframe)
                    # UNKNOWN fails open (don't halt trading on thin data); a known
                    # regime the strategy doesn't trade gates it out for this bar.
                    if reading.regime != UNKNOWN and reading.regime not in strategy.regimes:
                        log.info(
                            "candidate_gated strategy=%s symbol=%s tf=%s regime=%s "
                            "allowed=%s reason=%s raw_feed=%s",
                            name,
                            symbol,
                            timeframe,
                            reading.regime,
                            sorted(strategy.regimes),
                            reading.reason,
                            raw_feed,
                        )
                        if not raw_feed:
                            continue
                        # Raw feed on: evaluate anyway so the operator SEES the pure
                        # strategy signal, tagged so the gate records it and rejects
                        # it without AI/risk. It can never become a Signal.
                        regime_gated = True
                candidates = strategy.evaluate(window)
                for cand in candidates:
                    payload = cand.to_payload()
                    # Stale data outranks the regime tag: wrong prices are the more
                    # important thing to show on the row.
                    if stale_detail:
                        payload["preGatedBy"] = "stale_data"
                        payload["preGatedDetail"] = stale_detail
                    elif regime_gated:
                        payload["preGatedBy"] = "regime"
                    outcome = await _post_candidate(client, gate_url, payload)
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
