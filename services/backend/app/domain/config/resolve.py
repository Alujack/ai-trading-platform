"""Config resolver — port of `apps/api/src/config/resolve.ts`.

Reads the three scope rows (GLOBAL/STRATEGY/SYMBOL), layers them
most-specific-wins per field, caches the raw rows in Redis (busted on write),
and hands a single `EffectiveRiskConfig` / `ExecutionMode` to the gate, the risk
engine and the decider.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.enums import ExecutionMode
from ...db.models import ExecutionSetting, RiskConfig
from ...db.redis_client import cache_del, cache_get, cache_set
from .defaults import RISK_FIELDS, EffectiveRiskConfig, Scope, risk_defaults

log = logging.getLogger("backend.config")

RISK_CACHE_KEY = "config:risk:rows"
EXEC_CACHE_KEY = "config:exec:rows"
CACHE_TTL_S = 300  # safety net; writes bust the cache explicitly


@dataclass(slots=True)
class ExecRow:
    scope: str
    scopeKey: str
    mode: str


def _num(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        f = float(value)
    else:
        try:
            f = float(str(value))
        except (TypeError, ValueError):
            return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


async def _risk_rows(session: AsyncSession) -> list[dict[str, object]]:
    cached = await cache_get(RISK_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            pass
    rows = (await session.execute(select(RiskConfig))).scalars().all()
    payload = [
        {
            "scope": r.scope,
            "scopeKey": r.scopeKey,
            "enabled": r.enabled,
            **{f: (None if getattr(r, f) is None else float(getattr(r, f))) for f in RISK_FIELDS},
        }
        for r in rows
    ]
    await cache_set(RISK_CACHE_KEY, json.dumps(payload), CACHE_TTL_S)
    return payload


async def _exec_rows(session: AsyncSession) -> list[ExecRow]:
    cached = await cache_get(EXEC_CACHE_KEY)
    if cached:
        try:
            return [ExecRow(**r) for r in json.loads(cached)]
        except (json.JSONDecodeError, TypeError):
            pass
    rows = (
        await session.execute(
            select(ExecutionSetting.scope, ExecutionSetting.scopeKey, ExecutionSetting.mode)
        )
    ).all()
    out = [ExecRow(scope=r[0], scopeKey=r[1], mode=str(r[2].value if hasattr(r[2], "value") else r[2])) for r in rows]
    await cache_set(
        EXEC_CACHE_KEY,
        json.dumps([{"scope": r.scope, "scopeKey": r.scopeKey, "mode": r.mode} for r in out]),
        CACHE_TTL_S,
    )
    return out


async def bust_config_cache() -> None:
    """Invalidate both caches — call after any config/mode write."""
    await cache_del(RISK_CACHE_KEY, EXEC_CACHE_KEY)


def _pick(rows: list, scope: Scope, scope_key: str):
    wanted = "" if scope == "GLOBAL" else scope_key
    for row in rows:
        r_scope = row["scope"] if isinstance(row, dict) else row.scope
        r_key = row["scopeKey"] if isinstance(row, dict) else row.scopeKey
        if r_scope == scope and r_key == wanted:
            return row
    return None


async def resolve_risk_config(
    session: AsyncSession,
    strategy_name: str | None = None,
    symbol: str | None = None,
) -> EffectiveRiskConfig:
    """Layer SYMBOL ► STRATEGY ► GLOBAL ► code defaults, per field."""
    rows = await _risk_rows(session)
    global_row = _pick(rows, "GLOBAL", "")
    strat = _pick(rows, "STRATEGY", strategy_name) if strategy_name else None
    sym = _pick(rows, "SYMBOL", symbol) if symbol else None

    # A disabled override row is ignored entirely (acts as if absent).
    layers = [r for r in (sym, strat, global_row) if r and r.get("enabled")]

    out = risk_defaults()
    for field in RISK_FIELDS:
        for row in layers:
            value = _num(row.get(field))
            if value is not None:
                out = replace(out, **{field: value})
                break  # most-specific layer that has a value wins
    return out


async def resolve_execution_mode(
    session: AsyncSession,
    strategy_name: str | None = None,
    symbol: str | None = None,
) -> ExecutionMode:
    """Most-specific whole-row wins (unlike risk, mode is not field-layered)."""
    rows = await _exec_rows(session)
    sym = _pick(rows, "SYMBOL", symbol) if symbol else None
    if sym:
        return ExecutionMode(sym.mode)
    strat = _pick(rows, "STRATEGY", strategy_name) if strategy_name else None
    if strat:
        return ExecutionMode(strat.mode)
    global_row = _pick(rows, "GLOBAL", "")
    return ExecutionMode(global_row.mode) if global_row else ExecutionMode.CONFIRM


async def get_execution_map(session: AsyncSession) -> dict[str, object]:
    """`{ global, rows }` — the shape `GET /api/config/execution` returns."""
    rows = await _exec_rows(session)
    global_row = _pick(rows, "GLOBAL", "")
    return {
        "global": global_row.mode if global_row else ExecutionMode.CONFIRM.value,
        "rows": [{"scope": r.scope, "scopeKey": r.scopeKey, "mode": r.mode} for r in rows],
    }
