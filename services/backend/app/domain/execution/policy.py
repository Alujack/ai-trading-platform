"""The execution decider — port of `apps/api/src/execution/executionPolicy.ts`.

The single branch between the gate and execution. It applies the effective
`ExecutionMode` (OFF / AUTO / CONFIRM) and the pre-trade portfolio caps (max
open trades, max open risk, per-currency exposure). It runs AFTER the risk
engine has already approved the signal; it can only hold or route a trade, never
loosen a risk check. `OFF` and a tripped breaker override `AUTO` and `CONFIRM`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.serialization import num
from ...core.settings import get_settings
from ...db.enums import ExecutionMode, SignalStatus, TradeStatus
from ...db.models import Signal, Trade
from ...jobs.clock import start_of_utc_day
from ..config.defaults import SYMBOL_CURRENCIES
from ..config.resolve import resolve_execution_mode, resolve_risk_config

log = logging.getLogger("backend.policy")

DecisionAction = Literal["opened", "awaiting_approval", "held_off", "blocked"]


@dataclass(slots=True)
class Decision:
    mode: str
    action: DecisionAction
    reason: str | None = None
    tradeId: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "action": self.action}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.tradeId is not None:
            out["tradeId"] = self.tradeId
        return out


def _read_account() -> dict[str, float]:
    cfg = get_settings()
    return {
        "accountBalance": cfg.paper_account_balance,
        "peakBalance": cfg.paper_peak_balance,
    }


async def _today_realized_pnl(session: AsyncSession) -> dict[str, float]:
    """Today's realized P&L (UTC): net, and gross loss as a positive number."""
    rows = (
        await session.execute(
            select(Trade.profitLoss).where(
                Trade.status == TradeStatus.CLOSED, Trade.closedAt >= start_of_utc_day()
            )
        )
    ).all()
    net = 0.0
    loss = 0.0
    for (pl,) in rows:
        value = float(pl) if pl is not None else 0.0
        net += value
        if value < 0:
            loss += abs(value)
    return {"net": net, "loss": loss}


@dataclass(slots=True)
class BreakerState:
    tripped: bool
    reason: str | None = None


async def is_breaker_tripped_today(session: AsyncSession) -> BreakerState:
    """Has a circuit breaker tripped today?

    When true, the decider forces effective OFF regardless of any AUTO setting
    (breaker > mode).
    """
    cfg = await resolve_risk_config(session)
    account = _read_account()
    balance = account["accountBalance"]
    peak = account["peakBalance"]

    today = await _today_realized_pnl(session)
    daily_limit = balance * (cfg.dailyLossLimitPct / 100)
    if today["loss"] > daily_limit:
        return BreakerState(
            True, f"daily-loss {today['loss']:.0f} > limit {daily_limit:.0f}"
        )

    # Profit target: bank the green day. Realized-only, so an open trade still
    # runs to its own exit — the breaker only stops NEW trades until next UTC day.
    profit_target = balance * (cfg.dailyProfitTargetPct / 100)
    if today["net"] >= profit_target:
        return BreakerState(
            True,
            f"daily profit target hit (+${today['net']:.2f} >= ${profit_target:.2f}) — done for today",
        )

    # Drawdown from peak using realized equity.
    realized = (
        await session.execute(
            select(func.sum(Trade.profitLoss)).where(Trade.status == TradeStatus.CLOSED)
        )
    ).scalar()
    equity = balance + num(realized)
    if peak > 0:
        dd_pct = ((peak - equity) / peak) * 100
        if dd_pct > cfg.maxDrawdownPct:
            return BreakerState(
                True, f"drawdown {dd_pct:.1f}% > max {_fmt(cfg.maxDrawdownPct)}%"
            )
    return BreakerState(False)


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


async def _portfolio_cap_block(session: AsyncSession, signal: Signal) -> str | None:
    """Pre-trade portfolio caps. Returns a reason string when a cap is hit, else None."""
    cfg = await resolve_risk_config(session, signal.strategyName, signal.symbol)
    balance = _read_account()["accountBalance"]

    # Frequency caps are PER-STRATEGY: each strategy's maxTradesPerDay /
    # maxOpenTrades meters its own trades (a stacking scalper taking its 5th add
    # must not consume a swing strategy's one-a-day shot, and vice versa).
    # Exposure caps below (open risk, per-currency) stay PORTFOLIO-WIDE.
    opened_today = (
        await session.execute(
            select(func.count())
            .select_from(Trade)
            .join(Signal, Trade.signalId == Signal.id)
            .where(
                Trade.openedAt >= start_of_utc_day(),
                Signal.strategyName == signal.strategyName,
            )
        )
    ).scalar() or 0
    if opened_today >= cfg.maxTradesPerDay:
        return (
            f"daily trade limit reached ({opened_today}/{_fmt(cfg.maxTradesPerDay)})"
            " — stopped for today"
        )

    open_trades = (
        (await session.execute(select(Trade).where(Trade.status == TradeStatus.OPEN)))
        .scalars()
        .all()
    )

    open_same_strategy = [
        t for t in open_trades if t.signal.strategyName == signal.strategyName
    ]
    if len(open_same_strategy) >= cfg.maxOpenTrades:
        return f"max open trades reached ({len(open_same_strategy)}/{_fmt(cfg.maxOpenTrades)})"

    this_risk = balance * (cfg.riskPerTradePct / 100)
    open_risk = sum(num(t.riskAmount) for t in open_trades)
    max_open_risk = balance * (cfg.maxOpenRiskPct / 100)
    if open_risk + this_risk > max_open_risk + 1e-6:
        return (
            f"max open risk reached (${open_risk + this_risk:.0f} > ${max_open_risk:.0f})"
        )

    # Per-currency exposure: sum risk by base/quote currency of open positions.
    per_currency: dict[str, float] = {}
    for t in open_trades:
        for ccy in SYMBOL_CURRENCIES.get(t.signal.symbol, []):
            per_currency[ccy] = per_currency.get(ccy, 0.0) + num(t.riskAmount)
    max_per_ccy = balance * (cfg.maxRiskPerCurrencyPct / 100)
    for ccy in SYMBOL_CURRENCIES.get(signal.symbol, []):
        if per_currency.get(ccy, 0.0) + this_risk > max_per_ccy + 1e-6:
            return f"per-currency cap on {ccy} (${max_per_ccy:.0f})"

    return None


async def decide_execution(session: AsyncSession, signal: Signal) -> Decision:
    """Decide what to do with a freshly-persisted PENDING signal.

    OFF     → leave PENDING, logged, no trade (resumable within TTL)
    AUTO    → open immediately, subject to portfolio caps
    CONFIRM → create an Approval + send a Telegram alert
    """
    from ...integrations.telegram.approvals import request_approval
    from .live_trade import open_live_trade
    from .paper_trading import open_paper_trade

    mode = await resolve_execution_mode(session, signal.strategyName, signal.symbol)

    breaker = await is_breaker_tripped_today(session)
    if breaker.tripped:
        return Decision(
            mode=ExecutionMode.OFF.value, action="held_off", reason=f"breaker: {breaker.reason}"
        )

    if mode == ExecutionMode.OFF:
        return Decision(mode=mode.value, action="held_off", reason="mode OFF")

    # Portfolio caps apply to AUTO and CONFIRM alike — block before acting.
    cap = await _portfolio_cap_block(session, signal)
    if cap:
        return Decision(mode=mode.value, action="blocked", reason=cap)

    if mode == ExecutionMode.AUTO:
        opener = open_live_trade if get_settings().is_live_broker else open_paper_trade
        result = await opener(session, signal.id)
        return Decision(
            mode=mode.value,
            action="opened" if result.status == "opened" else "blocked",
            reason=result.reason,
            tradeId=result.tradeId,
        )

    # CONFIRM
    cfg = await resolve_risk_config(session, signal.strategyName, signal.symbol)
    approval = await request_approval(session, signal, int(cfg.approvalTtlMin))
    return Decision(mode=mode.value, action="awaiting_approval", reason=approval.get("reason"))


@dataclass(slots=True)
class ReconcileSummary:
    scanned: int
    opened: int
    awaiting: int
    held: int
    blocked: int


async def reconcile_pending_signals(session: AsyncSession) -> ReconcileSummary:
    """Reconciliation pass for the cron loop.

    Picks up any PENDING signal that has no trade AND no approval yet (covers a
    missed webhook, an OFF→AUTO flip, or a restart between gate and decide) and
    runs it through the decider. Signals that already have an approval are
    intentionally skipped so the sweep never races a pending human decision.
    """
    pending = (
        (
            await session.execute(
                select(Signal)
                .where(
                    Signal.status == SignalStatus.PENDING,
                    ~Signal.trades.any(),
                    ~Signal.approval.has(),
                )
                .order_by(Signal.createdAt.asc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    summary = ReconcileSummary(scanned=len(pending), opened=0, awaiting=0, held=0, blocked=0)
    for sig in pending:
        try:
            decision = await decide_execution(session, sig)
            if decision.action == "opened":
                summary.opened += 1
            elif decision.action == "awaiting_approval":
                summary.awaiting += 1
            elif decision.action == "held_off":
                summary.held += 1
            else:
                summary.blocked += 1
            log.info(
                "[reconcile] signal=%s %s/%s mode=%s action=%s%s",
                sig.id,
                sig.symbol,
                sig.timeframe,
                decision.mode,
                decision.action,
                f' reason="{decision.reason}"' if decision.reason else "",
            )
        except Exception as exc:
            log.error("[reconcile] signal=%s failed: %s", sig.id, exc)
    return summary
