"""Paper execution engine — port of `apps/api/src/execution/paperTrading.ts`.

Opens a `Trade` against an approved `Signal`, marks open positions to the latest
price, and on exit writes the close + `Journal` entry in one transaction so a
closed trade can never exist without its journal (CLAUDE.md: every trade signal
must be journaled with reasoning).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...core.serialization import dec, iso, num_or_nan
from ...core.settings import get_settings
from ...db.enums import SignalStatus, TradeStatus
from ...db.models import Candle, Journal, Signal, Trade
from ...db.redis_client import cache_get
from ...integrations.ai import client as ai
from ...integrations.ai.schemas import TradeReviewRequest
from ...jobs.clock import naive_utcnow
from ..risk.engine import calculate_position_size

log = logging.getLogger("backend.paper")

WEEKLY_REVIEW_WINDOW_DAYS = 7
WEEKLY_REVIEW_MAX_TRADES = 100


def _read_account_state() -> dict[str, Any]:
    cfg = get_settings()
    return {
        "userId": cfg.paper_user_id,
        "accountBalance": cfg.paper_account_balance,
        "peakBalance": cfg.paper_peak_balance,
        "riskPercent": cfg.paper_risk_percent,
    }


async def fetch_current_price(
    session: AsyncSession, symbol: str, timeframe: str
) -> float | None:
    """Latest mark: the Redis price tick if present, else the newest candle close."""
    cached = await cache_get(f"price:{symbol}")
    if cached:
        try:
            value = float(cached)
            if value > 0:
                return value
        except ValueError:
            pass  # fall through to the candle
    close = (
        await session.execute(
            select(Candle.close)
            .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
            .order_by(Candle.timestamp.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if close is None:
        return None
    value = float(close)
    return value if value == value else None


@dataclass(slots=True)
class OpenResult:
    status: str  # "opened" | "skipped"
    reason: str | None = None
    tradeId: str | None = None


async def open_paper_trade(session: AsyncSession, signal_id: str) -> OpenResult:
    """Size via the risk engine and open a paper `Trade` for an approved signal."""
    if get_settings().api_shadow_mode:
        return OpenResult("skipped", "shadow_mode_no_execution")

    signal = (
        await session.execute(select(Signal).where(Signal.id == signal_id))
    ).scalar_one_or_none()
    if signal is None:
        return OpenResult("skipped", "signal_not_found")
    if signal.trades:
        return OpenResult("skipped", "already_has_trade")
    if signal.status != SignalStatus.PENDING:
        return OpenResult("skipped", f"signal_status_{signal.status.value}")

    entry = num_or_nan(signal.entryPrice)
    stop = num_or_nan(signal.stopLoss)
    if entry != entry or stop != stop or entry == stop:
        return OpenResult("skipped", "invalid_levels")

    account = _read_account_state()
    try:
        sized = calculate_position_size(
            account["accountBalance"], account["riskPercent"], entry, stop
        )
    except ValueError as exc:
        return OpenResult("skipped", f"position_size_error: {exc}")

    trade = Trade(
        id=new_id(),
        signalId=signal.id,
        entryPrice=dec(entry, 8),
        positionSize=dec(sized.lotSize, 8),
        riskAmount=dec(sized.riskAmount, 2),
        status=TradeStatus.OPEN,
        openedAt=naive_utcnow(),
    )
    signal.status = SignalStatus.ACTIVE
    session.add(trade)
    await session.commit()

    return OpenResult("opened", tradeId=trade.id)


@dataclass(slots=True)
class SweepSummary:
    scanned: int
    opened: int
    skipped: int


async def sweep_pending_signals(session: AsyncSession) -> SweepSummary:
    """Open any PENDING signal that has no trade yet (paper path)."""
    pending = (
        (
            await session.execute(
                select(Signal)
                .where(Signal.status == SignalStatus.PENDING, ~Signal.trades.any())
                .order_by(Signal.createdAt.asc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    opened = 0
    skipped = 0
    for sig in pending:
        result = await open_paper_trade(session, sig.id)
        if result.status == "opened":
            opened += 1
            log.info(
                "[paperTrading] opened trade %s for signal %s %s/%s",
                result.tradeId,
                sig.id,
                sig.symbol,
                sig.timeframe,
            )
        else:
            skipped += 1
            log.info(
                '[paperTrading] skip signal %s (%s/%s) reason="%s"',
                sig.id,
                sig.symbol,
                sig.timeframe,
                result.reason or "",
            )
    return SweepSummary(scanned=len(pending), opened=opened, skipped=skipped)


@dataclass(slots=True)
class CloseDecision:
    exitPrice: float
    outcome: str  # "win" | "loss"


def evaluate_exit(
    direction: str, price: float, take_profit: float, stop_loss: float
) -> CloseDecision | None:
    """Has the mark price crossed the stop or the target? Pure, so it is unit-tested."""
    if direction == "LONG":
        if price <= stop_loss:
            return CloseDecision(exitPrice=stop_loss, outcome="loss")
        if price >= take_profit:
            return CloseDecision(exitPrice=take_profit, outcome="win")
        return None
    if price >= stop_loss:
        return CloseDecision(exitPrice=stop_loss, outcome="loss")
    if price <= take_profit:
        return CloseDecision(exitPrice=take_profit, outcome="win")
    return None


@dataclass(slots=True)
class MonitorSummary:
    inspected: int
    closed: int
    unchanged: int
    noPrice: int


async def _review_closed_trade(
    signal: Signal,
    entry: float,
    exit_price: float,
    pnl: float,
    r_multiple: float,
    exit_reason: str,
    opened_at,
    closed_at,
) -> Any | None:
    """Grade a just-closed trade on PROCESS (not P&L) and extract one lesson.

    Best-effort: a review failure must never block the trade close, so this
    returns None and the close proceeds.
    """
    try:
        request = TradeReviewRequest.model_validate(
            {
                "trade": {
                    "symbol": signal.symbol,
                    "direction": signal.direction.value,
                    "strategyName": signal.strategyName,
                    "entryPrice": entry,
                    "stopLoss": num_or_nan(signal.stopLoss),
                    "takeProfit": num_or_nan(signal.takeProfit),
                    "exitPrice": exit_price,
                    "profitLoss": pnl,
                    "rMultiple": r_multiple,
                    "exitReason": exit_reason,
                    "openedAt": iso(opened_at),
                    "closedAt": iso(closed_at),
                    "plannedReasoning": signal.aiReasoning,
                },
                "candles": [],
                "indicators": [],
            }
        )
        return await ai.trade_review(request)
    except Exception as exc:  # noqa: BLE001
        log.error("[paperTrading] trade-review unavailable: %s", exc)
        return None


def format_ai_review(review: Any | None, fallback: str) -> str:
    """Render the AI post-mortem into the `Journal.aiReview` line."""
    if review is None:
        return fallback
    worked = "; ".join(review.whatWorked) or "—"
    failed = "; ".join(review.whatFailed) or "—"
    return (
        f"Grade {review.grade} ({review.outcome}). {review.why} "
        f"Worked: {worked}. Failed: {failed}. Lesson: {review.lesson}"
    )


async def monitor_open_trades(session: AsyncSession) -> MonitorSummary:
    """Mark open paper trades to the latest price and close any that hit SL/TP."""
    open_trades = (
        (await session.execute(select(Trade).where(Trade.status == TradeStatus.OPEN)))
        .scalars()
        .all()
    )

    closed = 0
    unchanged = 0
    no_price = 0

    for trade in open_trades:
        sig = trade.signal
        price = await fetch_current_price(session, sig.symbol, sig.timeframe)
        if price is None:
            no_price += 1
            log.info(
                "[paperTrading] no_price trade=%s %s/%s — skipping",
                trade.id,
                sig.symbol,
                sig.timeframe,
            )
            continue

        decision = evaluate_exit(
            sig.direction.value, price, num_or_nan(sig.takeProfit), num_or_nan(sig.stopLoss)
        )
        if decision is None:
            unchanged += 1
            continue

        entry = num_or_nan(trade.entryPrice)
        size = num_or_nan(trade.positionSize)
        direction_sign = 1 if sig.direction.value == "LONG" else -1
        pnl = (decision.exitPrice - entry) * size * direction_sign

        # R-multiple (net P&L ÷ risk) and a deterministic outcome — both computed
        # here so expectancy tracking works even if the AI review is unavailable.
        risk_amount = num_or_nan(trade.riskAmount)
        r_multiple = pnl / risk_amount if risk_amount == risk_amount and risk_amount > 0 else 0.0
        outcome = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
        closed_at = naive_utcnow()

        review = await _review_closed_trade(
            sig, entry, decision.exitPrice, pnl, r_multiple, decision.outcome,
            trade.openedAt, closed_at,
        )

        notes = (
            f"Auto-closed by paper trading engine. {sig.direction.value} {sig.symbol} "
            f"{sig.timeframe}. Outcome: {outcome}. "
            f"Entry {entry:.5f} → Exit {decision.exitPrice:.5f}. "
            f"Size {size:.4f}, P&L ${pnl:.2f}, R {r_multiple:.2f}."
        )
        ai_review = format_ai_review(review, "(per-trade AI review unavailable at close)")

        trade.exitPrice = dec(decision.exitPrice, 8)
        trade.profitLoss = dec(pnl, 2)
        trade.status = TradeStatus.CLOSED
        trade.closedAt = closed_at
        sig.status = SignalStatus.CLOSED
        session.add(
            Journal(
                id=new_id(),
                tradeId=trade.id,
                notes=notes,
                aiReview=ai_review,
                grade=getattr(review, "grade", None),
                outcome=outcome,
                lesson=getattr(review, "lesson", None),
                rMultiple=dec(r_multiple, 4),
                createdAt=closed_at,
            )
        )
        await session.commit()

        closed += 1
        log.info(
            "[paperTrading] closed trade=%s %s/%s %s pnl=$%.2f R=%.2f grade=%s",
            trade.id,
            sig.symbol,
            sig.timeframe,
            outcome,
            pnl,
            r_multiple,
            getattr(review, "grade", None) or "n/a",
        )

    return MonitorSummary(
        inspected=len(open_trades), closed=closed, unchanged=unchanged, noPrice=no_price
    )


async def run_weekly_journal_review(session: AsyncSession) -> dict[str, Any]:
    """Weekly journal review: patterns, critique and (human-approved) config proposals."""
    from .review_agent import collect_tunables, process_review_proposals
    from ...integrations.ai.schemas import JournalReviewRequest

    cutoff = naive_utcnow() - timedelta(days=WEEKLY_REVIEW_WINDOW_DAYS)
    trades = (
        (
            await session.execute(
                select(Trade)
                .where(Trade.status == TradeStatus.CLOSED, Trade.closedAt >= cutoff)
                .order_by(Trade.closedAt.asc())
                .limit(WEEKLY_REVIEW_MAX_TRADES)
            )
        )
        .scalars()
        .all()
    )

    if not trades:
        log.info("[paperTrading] weekly review skipped — no closed trades in last 7 days")
        return {"status": "skipped", "reason": "no_trades", "tradeCount": 0}

    # Tunables give the reviewer permission to PROPOSE config changes — proposals
    # are journaled + human-approved, never auto-applied. Best-effort: a failure
    # here just means no proposals.
    tunables: list[dict[str, Any]] = []
    try:
        tunables = await collect_tunables(session)
    except Exception as exc:  # noqa: BLE001
        log.warning("[paperTrading] weekly review tunables unavailable: %s", exc)

    pnls = [float(t.profitLoss) if t.profitLoss is not None else 0.0 for t in trades]
    stats = {
        "tradeCount": len(trades),
        "wins": len([p for p in pnls if p > 0]),
        "losses": len([p for p in pnls if p < 0]),
        "netPnL": round(sum(pnls), 2),
    }

    payload_trades = []
    for t in trades:
        journal = min(t.journals, key=lambda j: j.createdAt) if t.journals else None
        payload_trades.append(
            {
                "symbol": t.signal.symbol,
                "direction": t.signal.direction.value,
                "entryPrice": num_or_nan(t.entryPrice),
                "exitPrice": None if t.exitPrice is None else float(t.exitPrice),
                "profitLoss": None if t.profitLoss is None else float(t.profitLoss),
                "openedAt": iso(t.openedAt),
                "closedAt": None if t.closedAt is None else iso(t.closedAt),
                "notes": journal.notes if journal else "",
                "emotions": journal.emotions if journal else None,
                "aiReview": journal.aiReview if journal else None,
            }
        )

    try:
        review = await ai.journal_review(
            JournalReviewRequest.model_validate(
                {"trades": payload_trades, "tunables": tunables, "stats": stats}
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.error("[paperTrading] weekly review unreachable: %s", exc)
        return {"status": "error", "reason": f"unreachable: {exc}", "tradeCount": len(trades)}

    proposals = list(getattr(review, "proposals", None) or [])
    log.info(
        "[paperTrading] weekly review ok trades=%d patterns=%d suggestions=%d proposals=%d",
        len(trades),
        len(review.patterns),
        len(review.suggestions),
        len(proposals),
    )

    # Journal + alert any config proposals; the agent never applies them itself.
    recommendations_created = 0
    if proposals:
        try:
            result = await process_review_proposals(session, proposals)
            recommendations_created = result["created"]
            log.info(
                "[paperTrading] weekly review proposals created=%d rejected=%d alerted=%d",
                result["created"],
                result["rejected"],
                result["alerted"],
            )
        except Exception as exc:  # noqa: BLE001
            log.error("[paperTrading] weekly review proposal processing failed: %s", exc)

    return {
        "status": "ok",
        "tradeCount": len(trades),
        "recommendationsCreated": recommendations_created,
        "patterns": review.patterns,
        "strengths": review.strengths,
        "weaknesses": review.weaknesses,
        "suggestions": review.suggestions,
        "proposals": [p.model_dump(by_alias=True) for p in proposals],
    }
