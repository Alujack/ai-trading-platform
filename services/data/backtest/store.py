"""Persist a backtest run to the BacktestRun table so the dashboard can show it.

Writes a single row holding the run config, the per-(strategy,symbol,timeframe)
metrics (with a verdict baked in for the UI), and each result's equity curve.
Uses raw asyncpg (the data service has no Prisma client); the `id` is generated
here because Prisma's `@default(cuid())` is client-side, not a DB default.
"""
from __future__ import annotations

import json
import math
import uuid
from decimal import Decimal
from typing import Any, Sequence

_INF_SENTINEL = 1e9  # Postgres jsonb rejects Infinity/NaN; the UI treats >1e6 as ∞.


def _json_safe(obj: Any) -> Any:
    """Replace non-finite floats so the payload is valid JSON for jsonb."""
    if isinstance(obj, float):
        if math.isinf(obj):
            return _INF_SENTINEL if obj > 0 else -_INF_SENTINEL
        if math.isnan(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj

import asyncpg

from .engine import RunResult
from .metrics import Metrics
from .report import verdict

_INSERT_SQL = """
INSERT INTO "BacktestRun" (
    "id", "label", "startingBalance", "riskPct", "costsApplied",
    "config", "results", "equityCurves"
) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb)
RETURNING "id"
"""


async def save_run(
    pool: asyncpg.Pool,
    *,
    label: str | None,
    starting_balance: float,
    risk_pct: float,
    costs_applied: bool,
    config: dict[str, Any],
    metrics: Sequence[Metrics],
    runs: Sequence[RunResult],
) -> str:
    # Bake the verdict into each metric so the UI doesn't have to re-derive it.
    results_json = [{**m.as_dict(), "verdict": verdict(m)} for m in metrics]

    curves: dict[str, list[list[Any]]] = {}
    for r in runs:
        key = f"{r.strategy}|{r.symbol}|{r.timeframe}"
        curves[key] = [[ts.isoformat(), float(eq)] for ts, eq in r.equity_curve]

    run_id = uuid.uuid4().hex
    async with pool.acquire() as conn:
        return await conn.fetchval(
            _INSERT_SQL,
            run_id,
            label,
            Decimal(str(starting_balance)),
            Decimal(str(risk_pct)),
            costs_applied,
            json.dumps(_json_safe(config), default=str),
            json.dumps(_json_safe(results_json), default=str),
            json.dumps(_json_safe(curves), default=str),
        )
