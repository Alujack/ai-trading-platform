"""Backtest runs + job control — port of `routes/backtests.routes.ts`.

`services/data/backtester.py` computes and persists everything; the API serves
stored rows and drives the child process.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ...core.serialization import iso
from ...db.models import BacktestRun
from ...domain.execution.backtest_runner import get_job_status, start_backtest
from ..dependencies import Db, Timeframe

router = APIRouter(tags=["backtests"])

BacktestSymbol = Literal["XAUUSD", "EURUSD", "BTCUSD"]
BacktestStrategy = Literal["trend_ema", "meanrev_rsi", "scalp_ema"]


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeframes: list[Timeframe] | None = Field(default=None, min_length=1, max_length=5)
    symbols: list[BacktestSymbol] | None = Field(default=None, min_length=1)
    strategies: list[BacktestStrategy] | None = Field(default=None, min_length=1)
    balance: float | None = Field(default=None, gt=0, le=1e9)
    risk: float | None = Field(default=None, gt=0, le=100)
    noCosts: bool | None = None
    label: str | None = Field(default=None, max_length=80)


@router.post("/api/backtests/run")
async def run_backtest(body: RunBody, response: Response) -> dict[str, Any]:
    """Kick off a backtest (spawns the Python backtester with --save-db). One at a time."""
    started = start_backtest(body.model_dump(exclude_none=True))
    if not started:
        response.status_code = 409
        return {"error": "A backtest is already running"}
    response.status_code = 202
    return {"status": "started", "job": get_job_status()}


@router.get("/api/backtests/run/status")
async def run_status() -> dict[str, Any]:
    """Status of the most recent / in-flight run (registered before /{id})."""
    return get_job_status()


@router.get("/api/backtests")
async def list_backtests(session: Db) -> dict[str, Any]:
    """Recent runs with their result metrics, but without the heavier equity curves."""
    runs = (
        (
            await session.execute(
                select(BacktestRun).order_by(BacktestRun.createdAt.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {
        "runs": [
            {
                "id": r.id,
                "label": r.label,
                "startingBalance": float(r.startingBalance),
                "riskPct": float(r.riskPct),
                "costsApplied": r.costsApplied,
                "config": r.config,
                "results": r.results,
                "createdAt": iso(r.createdAt),
            }
            for r in runs
        ]
    }


@router.get("/api/backtests/{run_id}")
async def get_backtest(run_id: str, session: Db, response: Response) -> dict[str, Any]:
    """Full detail for one run, including per-result equity curves for charting."""
    run = (
        await session.execute(select(BacktestRun).where(BacktestRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        response.status_code = 404
        return {"error": "Backtest run not found"}
    return {
        "id": run.id,
        "label": run.label,
        "startingBalance": float(run.startingBalance),
        "riskPct": float(run.riskPct),
        "costsApplied": run.costsApplied,
        "config": run.config,
        "results": run.results,
        "equityCurves": run.equityCurves,
        "createdAt": iso(run.createdAt),
    }
