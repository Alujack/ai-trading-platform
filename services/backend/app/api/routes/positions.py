"""Open positions + account summary — port of `routes/positions.routes.ts`.

Feeds the dashboard KPI strip and the top-bar equity. `positionSize` from the
gate is riskAmount / stopDistance, so $PnL is just priceDifference ×
positionSize — consistent with the risk model.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from ...core.serialization import iso, js_number, num
from ...core.settings import get_settings
from ...db.enums import TradeStatus
from ...db.models import Candle, Trade
from ...jobs.clock import start_of_utc_day
from ..dependencies import Db

router = APIRouter(tags=["trading"])


def _pnl(direction: str, entry: float, mark: float, size: float) -> float:
    sign = 1 if direction == "LONG" else -1
    return sign * (mark - entry) * size


def _round2(value: float) -> float | int | None:
    """Round to cents, then render the way a JavaScript number would."""
    return js_number(round(value, 2))


@router.get("/api/positions")
async def list_positions(session: Db) -> dict[str, Any]:
    cfg = get_settings()
    base_balance = cfg.paper_account_balance
    # Display-only: mirrors positions.routes.ts, which falls back to 5 here
    # even though the risk-engine cap falls back to 1.
    max_open = cfg.paper_max_open_trades_display

    open_trades = (
        (
            await session.execute(
                select(Trade)
                .where(Trade.status == TradeStatus.OPEN)
                .order_by(Trade.openedAt.desc())
            )
        )
        .scalars()
        .all()
    )

    # Latest mark price per symbol that has an open position.
    symbols = {t.signal.symbol for t in open_trades}
    marks: dict[str, float] = {}
    for symbol in symbols:
        close = (
            await session.execute(
                select(Candle.close)
                .where(Candle.symbol == symbol)
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if close is not None:
            marks[symbol] = num(close)

    unrealized = 0.0
    open_risk = 0.0
    positions = []
    for t in open_trades:
        entry = num(t.entryPrice)
        size = num(t.positionSize)
        mark = marks.get(t.signal.symbol, entry)
        upnl = _pnl(t.signal.direction.value, entry, mark, size)
        unrealized += upnl
        open_risk += num(t.riskAmount)
        positions.append(
            {
                "id": t.id,
                "symbol": t.signal.symbol,
                "direction": t.signal.direction.value,
                "size": js_number(size),
                "entry": js_number(entry),
                "mark": js_number(mark),
                "stopLoss": js_number(num(t.signal.stopLoss)),
                "takeProfit": js_number(num(t.signal.takeProfit)),
                "pnl": _round2(upnl),
                "openedAt": iso(t.openedAt),
            }
        )

    # Realized P&L: all-time and since the start of today (UTC).
    closed = (
        await session.execute(
            select(Trade.profitLoss, Trade.closedAt).where(Trade.status == TradeStatus.CLOSED)
        )
    ).all()
    start_of_day = start_of_utc_day()
    realized_total = 0.0
    realized_today = 0.0
    for profit_loss, closed_at in closed:
        value = num(profit_loss)
        realized_total += value
        if closed_at is not None and closed_at >= start_of_day:
            realized_today += value

    equity = base_balance + realized_total + unrealized
    day_pnl = realized_today + unrealized

    return {
        "account": {
            "baseBalance": js_number(base_balance),
            "equity": _round2(equity),
            "unrealized": _round2(unrealized),
            "realizedTotal": _round2(realized_total),
            "dayPnL": _round2(day_pnl),
            "dayPnLPct": _round2((day_pnl / base_balance) * 100) if base_balance else 0.0,
            "openRisk": _round2(open_risk),
            "openRiskPct": _round2((open_risk / equity) * 100) if equity else 0.0,
            "openCount": len(positions),
            "maxOpen": max_open,
        },
        "positions": positions,
    }
