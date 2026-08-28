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
from ...core.settings import get_settings
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
    this exact push order (inputs → daily loss → drawdown → RR → news).
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

    approved = len(reasons) == 0
    daily_loss_limit = data.accountBalance * (daily_loss_limit_pct / 100)

    # RiskLog is written whether or not the candidate was approved — a rejected
    # candidate still has to leave an audit trail. The one exception is shadow
    # mode: a shadow instance runs beside the real writer, so persisting here
    # would dual-write and double every row (plan §4 invariant 3: "Shadow
    # services are read-only and must not dual-write"). It emits a structured
    # parity log line instead.
    if get_settings().api_shadow_mode:
        _log(
            "SHADOW_riskLog",
            {
                "accountBalance": data.accountBalance,
                "riskPercent": data.riskPercent,
                "positionSize": position_size,
                "dailyLoss": data.todayLoss,
                "dailyLossLimit": daily_loss_limit,
                "circuitBreakerTripped": not approved,
            },
        )
    else:
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
        except Exception as exc:
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
