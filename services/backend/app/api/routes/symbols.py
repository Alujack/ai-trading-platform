"""Distinct traded symbols — port of `routes/symbols.routes.ts`."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import distinct, select

from ...db.models import Candle
from ..dependencies import Db

router = APIRouter(tags=["market"])


@router.get("/api/symbols")
async def list_symbols(session: Db) -> dict[str, list[str]]:
    rows = (
        await session.execute(select(distinct(Candle.symbol)).order_by(Candle.symbol.asc()))
    ).all()
    return {"symbols": [r[0] for r in rows]}
