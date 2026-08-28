"""The authoritative risk engine — port of `apps/api/src/risk/riskEngine.ts`.

CLAUDE.md rule: this must be called before any trade execution. Every decision
function is pure so the boundary cases (exact daily-loss equality, the RR
epsilon, the two-sided news window) are unit-tested directly; only
:func:`validate_trade` touches the database, and only to persist the `RiskLog`
row — which it writes whether or not the candidate was approved.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...core.serialization import dec
from ...db.models import RiskLog

log = logging.getLogger("backend.risk")

DAILY_LOSS_LIMIT_PCT = 3.0
MAX_DRAWDOWN_PCT = 10.0
MIN_RR = 2.0
# Tolerance so an exact-target ratio (e.g. 0.0100/0.0050) isn't rejected by
# floating-point rounding to 1.9999999998.
RR_EPSILON = 1e-9
NEWS_DEFAULT_BEFORE_MIN = 30
NEWS_DEFAULT_AFTER_MIN = 30

Impact = Literal["LOW", "MEDIUM", "HIGH"]
Direction = Literal["LONG", "SHORT"]


def _log(event: str, payload: dict[str, Any]) -> None:
    log.info("[risk] %s %s", event, json.dumps(payload, default=str, separators=(",", ":")))


@dataclass(slots=True)
class NewsLite:
    title: str
    impact: str
    scheduledAt: datetime


@dataclass(slots=True)
class Allowed:
    allowed: bool
    reason: str | None = None


@dataclass(slots=True)
class PositionSize:
    lotSize: float
    riskAmount: float


@dataclass(slots=True)
class RiskRewardResult:
    rr: float
    acceptable: bool


@dataclass(slots=True)
class NewsWindowResult:
    safe: bool
    nearestEvent: str | None


def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    entry_price: float,
    stop_loss: float,
) -> PositionSize:
    """risk$ ÷ stop-distance. Raises on any input that can't produce a size."""
    if not math.isfinite(account_balance) or account_balance <= 0:
        raise ValueError("accountBalance must be a positive number")
    if not math.isfinite(risk_percent) or risk_percent <= 0:
        raise ValueError("riskPercent must be a positive number")
    if not math.isfinite(entry_price) or not math.isfinite(stop_loss):
        raise ValueError("entryPrice and stopLoss must be finite numbers")
    distance = abs(entry_price - stop_loss)
    if distance <= 0:
        raise ValueError("entryPrice and stopLoss must differ")
    risk_amount = account_balance * (risk_percent / 100)
    lot_size = risk_amount / distance
    _log(
        "calculatePositionSize",
        {
            "accountBalance": account_balance,
            "riskPercent": risk_percent,
            "entryPrice": entry_price,
            "stopLoss": stop_loss,
            "riskAmount": risk_amount,
            "lotSize": lot_size,
        },
    )
    return PositionSize(lotSize=lot_size, riskAmount=risk_amount)


def check_daily_loss(
    user_id: str,
    today_loss: float,
    account_balance: float,
    limit_percent: float = DAILY_LOSS_LIMIT_PCT,
) -> Allowed:
    """Strictly greater-than: a loss exactly at the limit still passes."""
    limit = account_balance * (limit_percent / 100)
    tripped = today_loss > limit
    result = Allowed(False, "Daily loss limit reached") if tripped else Allowed(True)
    _log(
        "checkDailyLoss",
        {
            "userId": user_id,
            "todayLoss": today_loss,
            "accountBalance": account_balance,
            "limit": limit,
            "tripped": tripped,
        },
    )
    return result


def check_max_drawdown(
    peak_balance: float,
    current_balance: float,
    limit_percent: float = MAX_DRAWDOWN_PCT,
) -> Allowed:
    if not math.isfinite(peak_balance) or peak_balance <= 0:
        return Allowed(False, "Invalid peak balance")
    drawdown_pct = ((peak_balance - current_balance) / peak_balance) * 100
    tripped = drawdown_pct > limit_percent
    result = Allowed(False, "Max drawdown exceeded") if tripped else Allowed(True)
    _log(
        "checkMaxDrawdown",
        {
            "peakBalance": peak_balance,
            "currentBalance": current_balance,
            "drawdownPct": drawdown_pct,
            "limitPercent": limit_percent,
            "tripped": tripped,
        },
    )
    return result


def validate_risk_reward(
    entry: float, stop_loss: float, take_profit: float, min_rr: float = MIN_RR
) -> RiskRewardResult:
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    if risk <= 0:
        _log(
            "validateRiskReward",
            {"entry": entry, "stopLoss": stop_loss, "takeProfit": take_profit, "error": "risk_is_zero"},
        )
        return RiskRewardResult(rr=0, acceptable=False)
    rr = reward / risk
    result = RiskRewardResult(rr=rr, acceptable=rr >= min_rr - RR_EPSILON)
    _log(
        "validateRiskReward",
        {
            "entry": entry,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "rr": rr,
            "acceptable": result.acceptable,
        },
    )
    return result


def is_news_window(
    upcoming_news: list[NewsLite],
    minutes_before: float = NEWS_DEFAULT_BEFORE_MIN,
    minutes_after: float = NEWS_DEFAULT_AFTER_MIN,
    now: datetime | None = None,
) -> NewsWindowResult:
    """Two-sided blackout: blocks `minutes_before` ahead AND `minutes_after` past."""
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    high = [n for n in upcoming_news if n.impact == "HIGH"]
    nearest_event: str | None = None
    nearest_abs_min = math.inf
    in_window = False

    for event in high:
        at = event.scheduledAt
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        delta_min = (at - reference).total_seconds() / 60
        abs_min = abs(delta_min)
        if abs_min < nearest_abs_min:
            nearest_abs_min = abs_min
            nearest_event = event.title
        if -minutes_after <= delta_min <= minutes_before:
            in_window = True

    result = NewsWindowResult(safe=not in_window, nearestEvent=nearest_event)
    _log(
        "isNewsWindow",
        {
            "highImpactCount": len(high),
            "minutesBefore": minutes_before,
            "minutesAfter": minutes_after,
            "nearestEvent": nearest_event,
            "nearestAbsMin": None if math.isinf(nearest_abs_min) else nearest_abs_min,
            "safe": result.safe,
        },
    )
    return result


# =========================================================================
# Gold Multi-Strategy Risk Management
# =========================================================================

#: Max concurrent Gold positions (across all strategies).
GOLD_MAX_CONCURRENT = 3
#: Max positions in the same direction (prevent directional stacking).
GOLD_MAX_SAME_DIRECTION = 2
#: Daily profit target — once hit, reduce risk per trade.
GOLD_DAILY_TARGET_PCT = 1.5
#: Reduced risk per trade after hitting the daily target.
GOLD_REDUCED_RISK_PCT = 0.5
#: Max consecutive losses in a session before pausing.
GOLD_MAX_CONSECUTIVE_LOSSES = 3
#: Max risk allocated to a single session (Asian/London/NY).
GOLD_SESSION_RISK_BUDGET_PCT = 1.0


@dataclass(slots=True)
class OpenGoldPosition:
    symbol: str
    direction: str
    strategy: str
    session: str
    riskAmount: float


@dataclass(slots=True)
class GoldRiskContext:
    openPositions: list[OpenGoldPosition]
    direction: str
    strategyName: str
    session: str
    todayPnlPct: float
    sessionConsecutiveLosses: int
    sessionRiskUsed: float
    accountBalance: float


def validate_gold_risk(ctx: GoldRiskContext) -> list[str]:
    """Gold-specific multi-strategy constraints, IN ADDITION TO the base checks."""
    reasons: list[str] = []

    # 1. Max concurrent Gold positions.
    if len(ctx.openPositions) >= GOLD_MAX_CONCURRENT:
        reasons.append(
            f"Gold concurrent limit: {len(ctx.openPositions)}/{GOLD_MAX_CONCURRENT} positions already open"
        )

    # 2. Directional correlation guard — prevent stacking same direction.
    same_dir = [p for p in ctx.openPositions if p.direction == ctx.direction]
    if len(same_dir) >= GOLD_MAX_SAME_DIRECTION:
        reasons.append(
            f"Gold direction limit: {len(same_dir)}/{GOLD_MAX_SAME_DIRECTION} {ctx.direction} positions already open"
        )

    # 3. Consecutive loss circuit breaker — pause until next session.
    if ctx.sessionConsecutiveLosses >= GOLD_MAX_CONSECUTIVE_LOSSES:
        reasons.append(
            f"Gold session paused: {ctx.sessionConsecutiveLosses} consecutive losses in {ctx.session} session"
        )

    # 4. Session risk budget — max total risk per session.
    session_budget = ctx.accountBalance * (GOLD_SESSION_RISK_BUDGET_PCT / 100)
    if ctx.sessionRiskUsed >= session_budget:
        reasons.append(
            f"Gold session budget exhausted: ${ctx.sessionRiskUsed:.2f} / ${session_budget:.2f} in {ctx.session}"
        )

    # 5. Trailing daily target — log a warning, do not block.
    if ctx.todayPnlPct >= GOLD_DAILY_TARGET_PCT:
        _log(
            "goldRisk_dailyTargetHit",
            {
                "todayPnlPct": ctx.todayPnlPct,
                "threshold": GOLD_DAILY_TARGET_PCT,
                "recommendation": f"Reduce risk to {GOLD_REDUCED_RISK_PCT}% per trade",
            },
        )

    if reasons:
        _log(
            "goldRisk_blocked",
            {
                "strategy": ctx.strategyName,
                "direction": ctx.direction,
                "session": ctx.session,
                "reasons": reasons,
            },
        )
    return reasons


def get_gold_adjusted_risk(base_risk_pct: float, today_pnl_pct: float) -> float:
    """Reduced risk % once the Gold daily target is hit, else the base %."""
    if today_pnl_pct >= GOLD_DAILY_TARGET_PCT:
        _log(
            "goldRisk_reducedRisk",
            {
                "baseRiskPct": base_risk_pct,
                "adjustedRiskPct": GOLD_REDUCED_RISK_PCT,
                "reason": f"Daily target {GOLD_DAILY_TARGET_PCT}% hit (current: {today_pnl_pct:.2f}%)",
            },
        )
        return GOLD_REDUCED_RISK_PCT
    return base_risk_pct


# =========================================================================
# validateTrade
# =========================================================================


@dataclass(slots=True)
class RiskThresholds:
    """Runtime-resolved thresholds; an omitted field falls back to the constant."""

    minRR: float | None = None
    dailyLossLimitPct: float | None = None
    maxDrawdownPct: float | None = None
    newsBeforeMin: float | None = None
    newsAfterMin: float | None = None


@dataclass(slots=True)
class ValidateTradeInput:
    userId: str
    symbol: str
    entry: float
    stopLoss: float
    takeProfit: float
    accountBalance: float
    peakBalance: float
    todayLoss: float
    riskPercent: float
    upcomingNews: list[NewsLite] = field(default_factory=list)
    thresholds: RiskThresholds | None = None
    goldContext: GoldRiskContext | None = None


@dataclass(slots=True)
class ValidateTradeResult:
    approved: bool
    positionSize: float
    reasons: list[str]


async def validate_trade(
    session: AsyncSession, data: ValidateTradeInput
) -> ValidateTradeResult:
    """Run every pre-trade check and persist the `RiskLog` row either way.

    Reason ordering is load-bearing: `raw_feed.classify_gate_outcome` maps the
    joined reason string back to the layer that stopped the candidate, following
    this exact push order (inputs → daily loss → drawdown → RR → news → gold).
    """
    reasons: list[str] = []
    t = data.thresholds or RiskThresholds()
    min_rr = t.minRR if t.minRR is not None else MIN_RR
    daily_loss_limit_pct = (
        t.dailyLossLimitPct if t.dailyLossLimitPct is not None else DAILY_LOSS_LIMIT_PCT
    )
    max_drawdown_pct = t.maxDrawdownPct if t.maxDrawdownPct is not None else MAX_DRAWDOWN_PCT
    news_before_min = t.newsBeforeMin if t.newsBeforeMin is not None else NEWS_DEFAULT_BEFORE_MIN
    news_after_min = t.newsAfterMin if t.newsAfterMin is not None else NEWS_DEFAULT_AFTER_MIN

    position_size = 0.0
    try:
        position_size = calculate_position_size(
            data.accountBalance, data.riskPercent, data.entry, data.stopLoss
        ).lotSize
    except ValueError as exc:
        reasons.append(str(exc) or "Invalid position size inputs")

    daily = check_daily_loss(
        data.userId, data.todayLoss, data.accountBalance, daily_loss_limit_pct
    )
    if not daily.allowed and daily.reason:
        reasons.append(daily.reason)

    dd = check_max_drawdown(data.peakBalance, data.accountBalance, max_drawdown_pct)
    if not dd.allowed and dd.reason:
        reasons.append(dd.reason)

    rr = validate_risk_reward(data.entry, data.stopLoss, data.takeProfit, min_rr)
    if not rr.acceptable:
        reasons.append(f"Risk/reward {rr.rr:.2f} below minimum {_fmt_num(min_rr)}")

    news = is_news_window(data.upcomingNews, news_before_min, news_after_min)
    if not news.safe:
        reasons.append(
            f"Inside news window: {news.nearestEvent}"
            if news.nearestEvent
            else "Inside high-impact news window"
        )

    # --- Gold multi-strategy risk checks ---
    if data.goldContext is not None:
        reasons.extend(validate_gold_risk(data.goldContext))

    approved = len(reasons) == 0
    daily_loss_limit = data.accountBalance * (daily_loss_limit_pct / 100)

    try:
        session.add(
            RiskLog(
                id=new_id(),
                accountBalance=dec(data.accountBalance, 2),
                riskPercent=dec(data.riskPercent, 4),
                positionSize=dec(position_size, 8),
                dailyLoss=dec(data.todayLoss, 2),
                dailyLossLimit=dec(daily_loss_limit, 2),
                circuitBreakerTripped=not approved,
                createdAt=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await session.flush()
    except Exception as exc:  # noqa: BLE001 — the verdict must survive a log failure
        log.error("[risk] failed to persist RiskLog: %s", exc)

    _log(
        "validateTrade",
        {
            "userId": data.userId,
            "symbol": data.symbol,
            "approved": approved,
            "positionSize": position_size,
            "reasons": reasons,
        },
    )
    return ValidateTradeResult(approved=approved, positionSize=position_size, reasons=reasons)


def _fmt_num(value: float) -> str:
    """Render a threshold the way JS interpolation did (`2`, not `2.0`)."""
    return str(int(value)) if float(value).is_integer() else str(value)
