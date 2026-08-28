"""Signals: the gate endpoint, the list/detail reads, and the raw feed.

Port of `routes/signals.routes.ts`. Route order matters — `/api/signals/raw` is
registered BEFORE `/api/signals/{id}` so the literal path wins over the param
route, exactly as in the Express router.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from ...core.serialization import ser
from ...db.enums import RawVerdict, SignalStatus
from ...db.models import RawSignal, Signal
from ...domain.config.flags import RAW_FEED_FLAG, get_flag
from ...domain.signals.gate import SignalCandidate, gate_candidate
from ..dependencies import (
    Db,
    DirectionParam,
    RawVerdictParam,
    SignalStatusParam,
    Timeframe,
    is_blocked_only,
)
from ..errors import NotFoundError

router = APIRouter(tags=["signals"])


class SignalCandidateBody(BaseModel):
    """Mirrors `signalCandidateSchema` in `schemas/signals.schema.ts`."""

    model_config = ConfigDict(extra="forbid")

    strategyName: str = Field(min_length=1, max_length=50)
    symbol: str = Field(min_length=1, max_length=20)
    timeframe: Timeframe
    direction: DirectionParam
    entryPrice: float = Field(gt=0)
    stopLoss: float = Field(gt=0)
    takeProfit: float = Field(gt=0)
    confidence: float = Field(ge=0, le=100)
    reasoning: str = Field(min_length=1, max_length=4000)
    clientId: str | None = Field(default=None, min_length=1, max_length=64)
    cooldownMs: int | None = Field(default=None, ge=0, le=604_800_000)
    aiMinScore: float | None = Field(default=None, ge=0, le=100)
    #: Raw-feed only: an upstream layer already refused this candidate, so the
    #: gate records it and rejects it without evaluating. Never becomes a Signal.
    preGatedBy: Annotated[str, Field(pattern="^regime$")] | None = None


@router.post("/api/signals/candidate")
async def post_candidate(
    body: SignalCandidateBody, session: Db, response: Response
) -> dict[str, Any]:
    """Single AI + risk gate for every strategy.

    A strategy (Python or TS) POSTs a candidate here; only AI-approved,
    risk-approved candidates become PENDING.
    """
    result = await gate_candidate(session, SignalCandidate(**body.model_dump()))
    response.status_code = 201 if result.status == "generated" else 200
    return result.as_dict()


@router.get("/api/signals")
async def list_signals(
    session: Db,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: SignalStatusParam | None = None,
    symbol: str | None = Query(default=None, min_length=1, max_length=20),
) -> dict[str, Any]:
    filters = []
    if status:
        filters.append(Signal.status == SignalStatus(status))
    if symbol:
        filters.append(Signal.symbol == symbol)

    rows = (
        (
            await session.execute(
                select(Signal)
                .where(*filters)
                .order_by(Signal.createdAt.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.execute(select(func.count()).select_from(Signal).where(*filters))
    ).scalar() or 0

    return ser(
        {
            "data": [_signal_row(s) for s in rows],
            "pagination": {"limit": limit, "offset": offset, "total": total},
        }
    )


# The PURE strategy feed — every candidate as the strategy emitted it, with the
# verdict of each protection layer attached (blockedBy/blockedReason) instead of
# applied. Read-only and execution-free: these rows have no path to a Trade.
# Populated only while the raw_signal_feed flag is on.
#
# Registered BEFORE /api/signals/{id} so the literal path wins.
@router.get("/api/signals/raw")
async def list_raw_signals(
    session: Db,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    symbol: str | None = Query(default=None, min_length=1, max_length=20),
    strategy: str | None = Query(default=None, min_length=1, max_length=50),
    timeframe: Timeframe | None = None,
    verdict: RawVerdictParam | None = None,
    blockedOnly: str | None = Query(default=None, pattern="^(0|1|true|false)$"),
) -> dict[str, Any]:
    filters = []
    if symbol:
        filters.append(RawSignal.symbol == symbol)
    if strategy:
        filters.append(RawSignal.strategyName == strategy)
    if timeframe:
        filters.append(RawSignal.timeframe == timeframe)
    if verdict:
        filters.append(RawSignal.verdict == RawVerdict(verdict))
    # blockedOnly wins over an explicit verdict: "show me what automation didn't take".
    if is_blocked_only(blockedOnly):
        filters = [f for f in filters if f is not None]
        filters.append(RawSignal.verdict.in_([RawVerdict.REJECTED, RawVerdict.SKIPPED]))

    rows = (
        (
            await session.execute(
                select(RawSignal)
                .where(*filters)
                .order_by(RawSignal.lastSeenAt.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.execute(select(func.count()).select_from(RawSignal).where(*filters))
    ).scalar() or 0
    feed = await get_flag(session, RAW_FEED_FLAG)

    return ser(
        {
            "data": [_raw_signal_row(r) for r in rows],
            "feedEnabled": feed.enabled,
            "pagination": {"limit": limit, "offset": offset, "total": total},
        }
    )


@router.get("/api/signals/{signal_id}")
async def get_signal(signal_id: str, session: Db) -> dict[str, Any]:
    signal = (
        await session.execute(select(Signal).where(Signal.id == signal_id))
    ).scalar_one_or_none()
    if signal is None:
        raise NotFoundError()
    payload = _signal_row(signal)
    payload["trades"] = [
        {
            "id": t.id,
            "signalId": t.signalId,
            "entryPrice": t.entryPrice,
            "exitPrice": t.exitPrice,
            "positionSize": t.positionSize,
            "riskAmount": t.riskAmount,
            "profitLoss": t.profitLoss,
            "status": t.status,
            "openedAt": t.openedAt,
            "closedAt": t.closedAt,
            "externalOrderId": t.externalOrderId,
            "brokerFillPrice": t.brokerFillPrice,
            "broker": t.broker,
            "journals": [
                {
                    "id": j.id,
                    "tradeId": j.tradeId,
                    "notes": j.notes,
                    "aiReview": j.aiReview,
                    "emotions": j.emotions,
                    "grade": j.grade,
                    "outcome": j.outcome,
                    "lesson": j.lesson,
                    "rMultiple": j.rMultiple,
                    "createdAt": j.createdAt,
                }
                for j in sorted(t.journals, key=lambda j: j.createdAt)
            ],
        }
        for t in sorted(signal.trades, key=lambda t: t.openedAt)
    ]
    return ser(payload)


def _signal_row(s: Signal) -> dict[str, Any]:
    return {
        "id": s.id,
        "symbol": s.symbol,
        "timeframe": s.timeframe,
        "direction": s.direction,
        "entryPrice": s.entryPrice,
        "stopLoss": s.stopLoss,
        "takeProfit": s.takeProfit,
        "confidenceScore": s.confidenceScore,
        "aiReasoning": s.aiReasoning,
        "strategyName": s.strategyName,
        "status": s.status,
        "createdAt": s.createdAt,
    }


def _raw_signal_row(r: RawSignal) -> dict[str, Any]:
    return {
        "id": r.id,
        "symbol": r.symbol,
        "timeframe": r.timeframe,
        "direction": r.direction,
        "entryPrice": r.entryPrice,
        "stopLoss": r.stopLoss,
        "takeProfit": r.takeProfit,
        "confidence": r.confidence,
        "reasoning": r.reasoning,
        "strategyName": r.strategyName,
        "verdict": r.verdict,
        "blockedBy": r.blockedBy,
        "blockedReason": r.blockedReason,
        "signalId": r.signalId,
        "dedupeKey": r.dedupeKey,
        "seenCount": r.seenCount,
        "lastSeenAt": r.lastSeenAt,
        "createdAt": r.createdAt,
    }
