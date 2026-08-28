"""The single AI + risk gate — port of `apps/api/src/signals/gate.ts`.

Every strategy (Python or TS) funnels through :func:`gate_candidate`, which is
the one place that calls the AI validator and the risk engine, per the CLAUDE.md
rule "Risk engine must be called before any trade execution".

Shadow mode (`API_SHADOW_MODE=true`) runs the identical decision path but
refuses to write a `Signal`, open a trade, contact Telegram or reach MT5 — it
only reports what it *would* have decided, so decision parity can be validated
before ownership changes hands.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...core.realtime import publish_event
from ...core.serialization import dec, iso, num_or_nan, num_or_none
from ...core.settings import get_settings
from ...db.enums import Direction, SignalStatus, TradeStatus
from ...db.models import Candle, Indicator, NewsEvent, Signal, Trade
from ...integrations.ai import client as ai
from ...integrations.ai.schemas import ValidateSignalRequest
from ...jobs.clock import naive_utcnow, start_of_utc_day
from ..config.resolve import resolve_risk_config
from ..risk.engine import (
    NewsLite,
    RiskThresholds,
    ValidateTradeInput,
    validate_trade,
)
from . import raw_feed

log = logging.getLogger("backend.gate")

DEFAULT_AI_MIN_SCORE = 70
CANDLE_LOOKBACK = 50
INDICATOR_LOOKBACK = 10
NEWS_LOOKAHEAD = 5
#: Two-sided fetch window: `is_news_window` also blocks for newsAfterMin AFTER a
#: release, so recent-past events must be included — a future-only query made
#: the after-window dead code. 4h covers the max configurable after-window.
NEWS_PAST_WINDOW = timedelta(hours=4)

GateStatus = Literal["skipped", "rejected", "generated"]


@dataclass(slots=True)
class SignalCandidate:
    """A strategy's proposed trade, before it has been validated."""

    strategyName: str
    symbol: str
    timeframe: str
    direction: str
    entryPrice: float
    stopLoss: float
    takeProfit: float
    #: Strategy's own pre-AI confidence (0–100); informational, not the gate.
    confidence: float
    #: Human-readable strategy rationale, folded into the stored aiReasoning.
    reasoning: str
    #: Deterministic id for idempotency (e.g. a per-bar hash). Optional.
    clientId: str | None = None
    #: If set (>0), reject a new candidate while an open signal for the same
    #: (symbol, timeframe, strategy) is younger than this many ms.
    cooldownMs: int | None = None
    #: AI score floor; defaults to 70.
    aiMinScore: float | None = None
    #: Set by the runner when an UPSTREAM layer already refused this candidate
    #: and it is being posted for the raw feed only (today: "regime"). The gate
    #: records it raw, then rejects it without running AI/risk — it can never
    #: become a Signal, so a raw-feed candidate is never one step from execution.
    preGatedBy: str | None = None


@dataclass(slots=True)
class GateResult:
    status: GateStatus
    reason: str | None = None
    signalId: str | None = None
    score: float | None = None
    #: Shadow-mode only: the decision this run would have taken.
    shadow: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.signalId is not None:
            out["signalId"] = self.signalId
        if self.score is not None:
            out["score"] = self.score
        if self.shadow is not None:
            out["shadow"] = self.shadow
        return out


def _read_account_state() -> dict[str, Any]:
    cfg = get_settings()
    return {
        "userId": cfg.paper_user_id,
        "accountBalance": cfg.paper_account_balance,
        "peakBalance": cfg.paper_peak_balance,
        "riskPercent": cfg.paper_risk_percent,
    }


async def _compute_today_loss(session: AsyncSession) -> float:
    """Today's gross realized loss (UTC), as a positive number."""
    rows = (
        await session.execute(
            select(Trade.profitLoss).where(
                Trade.status == TradeStatus.CLOSED,
                Trade.closedAt >= start_of_utc_day(),
            )
        )
    ).all()
    total = 0.0
    for (pl,) in rows:
        value = float(pl) if pl is not None else 0.0
        if value < 0:
            total += abs(value)
    return total


async def gate_candidate(session: AsyncSession, candidate: SignalCandidate) -> GateResult:
    """The public entry point every strategy hits.

    Wraps the real gate with the raw ("layers off") feed: the untouched candidate
    is recorded first, the full layer stack then runs exactly as before, and the
    verdict is stamped back onto the raw row so the dashboard can show WHICH
    layer stopped it.

    The raw feed is observe-only and cannot change a verdict: :func:`_run_gate`
    below is untouched by the flag, so AI validation and the risk engine still
    gate every Signal that execution can ever act on. With the flag off this is a
    straight pass-through.
    """
    raw_id = await raw_feed.record_raw_candidate(session, candidate)

    # Posted for visibility only — an upstream layer already refused it. Never
    # evaluate or persist it as a Signal.
    if candidate.preGatedBy:
        result = GateResult(status="rejected", reason=f"pre_gated_{candidate.preGatedBy}")
        await raw_feed.stamp_raw_verdict(session, raw_id, result)
        return result

    result = await _run_gate(session, candidate)
    await raw_feed.stamp_raw_verdict(session, raw_id, result)
    return result


async def _run_gate(session: AsyncSession, candidate: SignalCandidate) -> GateResult:
    """Validate through AI + risk and persist a PENDING Signal when both pass."""
    cfg_settings = get_settings()
    shadow = cfg_settings.api_shadow_mode
    strategy_name = candidate.strategyName
    symbol = candidate.symbol
    timeframe = candidate.timeframe
    direction = candidate.direction

    # Idempotency: a candidate carrying a clientId is the same trade if we've
    # already stored it (lets per-bar strategies re-emit safely).
    if candidate.clientId:
        existing = (
            await session.execute(select(Signal.id).where(Signal.id == candidate.clientId))
        ).scalar_one_or_none()
        if existing:
            return GateResult(status="skipped", reason="idempotent_duplicate")

    # Cooldown: suppress a fresh signal while one is still open for this strategy.
    if candidate.cooldownMs and candidate.cooldownMs > 0:
        recent = (
            await session.execute(
                select(Signal)
                .where(
                    Signal.symbol == symbol,
                    Signal.timeframe == timeframe,
                    Signal.strategyName == strategy_name,
                    Signal.status.in_([SignalStatus.PENDING, SignalStatus.ACTIVE]),
                )
                .order_by(Signal.createdAt.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent is not None:
            age_ms = (naive_utcnow() - recent.createdAt).total_seconds() * 1000
            if age_ms < candidate.cooldownMs:
                return GateResult(status="skipped", reason="cooldown_active")

    candles = (
        (
            await session.execute(
                select(Candle)
                .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
                .order_by(Candle.timestamp.desc())
                .limit(CANDLE_LOOKBACK)
            )
        )
        .scalars()
        .all()
    )
    if len(candles) < 10:
        return GateResult(status="skipped", reason=f"insufficient_candles={len(candles)}")

    indicators = (
        (
            await session.execute(
                select(Indicator)
                .where(
                    Indicator.symbol == symbol,
                    Indicator.timeframe == timeframe,
                    Indicator.timestamp.in_([c.timestamp for c in candles]),
                )
                .order_by(Indicator.timestamp.desc())
            )
        )
        .scalars()
        .all()
    )

    upcoming_news = (
        (
            await session.execute(
                select(NewsEvent)
                .where(NewsEvent.scheduledAt > naive_utcnow() - NEWS_PAST_WINDOW)
                .order_by(NewsEvent.scheduledAt.asc())
                .limit(NEWS_LOOKAHEAD + 5)
            )
        )
        .scalars()
        .all()
    )

    # Resolve runtime config for this (strategy, symbol) — most-specific-wins.
    cfg = await resolve_risk_config(session, strategy_name, symbol)

    ai_request = ValidateSignalRequest.model_validate(
        {
            "signal": {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "entryPrice": candidate.entryPrice,
                "stopLoss": candidate.stopLoss,
                "takeProfit": candidate.takeProfit,
                "confidenceScore": round(candidate.confidence),
                "aiReasoning": candidate.reasoning,
            },
            "candles": [
                {
                    "timestamp": iso(c.timestamp),
                    "open": num_or_nan(c.open),
                    "high": num_or_nan(c.high),
                    "low": num_or_nan(c.low),
                    "close": num_or_nan(c.close),
                    "volume": num_or_nan(c.volume),
                }
                for c in candles
            ],
            "indicators": [
                {
                    "timestamp": iso(i.timestamp),
                    "rsi": num_or_none(i.rsi),
                    "ema20": num_or_none(i.ema20),
                    "ema50": num_or_none(i.ema50),
                    "ema200": num_or_none(i.ema200),
                    "atr": num_or_none(i.atr),
                }
                for i in indicators[:INDICATOR_LOOKBACK]
            ],
            "upcomingNews": [
                {
                    "title": n.title,
                    "impact": n.impact.value,
                    "currency": n.currency,
                    "scheduledAt": iso(n.scheduledAt),
                }
                for n in upcoming_news
            ],
        }
    )

    try:
        ai_result = await ai.validate_signal(ai_request)
    except Exception as exc:
        # Same reason prefix the Express gate produced, so `classify_gate_outcome`
        # still tags this `ai_unreachable`.
        return GateResult(status="skipped", reason=f"ai_service_unreachable: {exc}")

    min_score = (
        candidate.aiMinScore
        if candidate.aiMinScore is not None
        else (cfg.aiMinScore if cfg.aiMinScore is not None else DEFAULT_AI_MIN_SCORE)
    )
    if not isinstance(ai_result.score, (int, float)) or ai_result.score < min_score:
        return GateResult(
            status="rejected",
            reason=f"ai_score_too_low score={ai_result.score}",
            score=ai_result.score,
        )
    # The AI's *judgment* gates the trade, not just its number. A high score with
    # an explicit `approved: false` (e.g. the model flags bad structure or event
    # risk in its reasoning) must still be rejected — otherwise the reasoning
    # layer is decorative.
    if ai_result.approved is False:
        why = "; ".join(ai_result.concerns) if ai_result.concerns else ai_result.reasoning
        return GateResult(
            status="rejected",
            reason=f"ai_not_approved: {why or 'no reason given'}",
            score=ai_result.score,
        )

    news_lite = [
        NewsLite(title=n.title, impact=n.impact.value, scheduledAt=n.scheduledAt)
        for n in upcoming_news
    ]

    account = _read_account_state()
    today_loss = await _compute_today_loss(session)
    risk = await validate_trade(
        session,
        ValidateTradeInput(
            userId=account["userId"],
            symbol=symbol,
            entry=candidate.entryPrice,
            stopLoss=candidate.stopLoss,
            takeProfit=candidate.takeProfit,
            accountBalance=account["accountBalance"],
            peakBalance=account["peakBalance"],
            todayLoss=today_loss,
            riskPercent=cfg.riskPerTradePct,
            upcomingNews=news_lite,
            thresholds=RiskThresholds(
                minRR=cfg.minRR,
                dailyLossLimitPct=cfg.dailyLossLimitPct,
                maxDrawdownPct=cfg.maxDrawdownPct,
                newsBeforeMin=cfg.newsBeforeMin,
                newsAfterMin=cfg.newsAfterMin,
            ),
        ),
    )
    await session.commit()  # persist the RiskLog row regardless of the verdict

    if not risk.approved:
        return GateResult(
            status="rejected",
            reason=f"risk_rejected: {'; '.join(risk.reasons)}",
            score=ai_result.score,
        )

    concerns_line = "; ".join(ai_result.concerns) if ai_result.concerns else "none"
    reasoning = "\n".join(
        [
            f"Strategy {strategy_name} ({direction}):",
            f"  {candidate.reasoning}",
            "",
            f"AI score: {ai_result.score}",
            f"AI reasoning: {ai_result.reasoning}",
            f"AI concerns: {concerns_line}",
            "",
            f"Risk approved. Position size {risk.positionSize:.8f} units.",
        ]
    )

    if shadow:
        # Shadow mode stops here: the decision is computed and reported, but no
        # Signal is written and no execution path is touched.
        log.info(
            "[gate] SHADOW would_generate strategy=%s %s/%s score=%s size=%.8f",
            strategy_name,
            symbol,
            timeframe,
            ai_result.score,
            risk.positionSize,
        )
        return GateResult(
            status="rejected",
            reason="shadow_mode: decision computed, no write",
            score=ai_result.score,
            shadow={
                "wouldGenerate": True,
                "score": ai_result.score,
                "positionSize": risk.positionSize,
                "riskApproved": True,
                "reasons": risk.reasons,
                "minScore": min_score,
            },
        )

    try:
        signal = Signal(
            id=candidate.clientId or new_id(),
            symbol=symbol,
            timeframe=timeframe,
            direction=Direction(direction),
            entryPrice=dec(candidate.entryPrice, 8),
            stopLoss=dec(candidate.stopLoss, 8),
            takeProfit=dec(candidate.takeProfit, 8),
            confidenceScore=round(ai_result.score),
            aiReasoning=reasoning,
            strategyName=strategy_name,
            status=SignalStatus.PENDING,
            createdAt=naive_utcnow(),
        )
        session.add(signal)
        await session.commit()
    except IntegrityError:
        # Lost an idempotency race; the other writer already stored it.
        await session.rollback()
        return GateResult(status="skipped", reason="idempotent_duplicate")

    await publish_event("signal", symbol=symbol, timeframe=timeframe)

    # Execution decision (OFF / AUTO / CONFIRM). Runs AFTER risk approval — never
    # instead of it. Best-effort: a decider hiccup must not undo the signal we
    # just persisted (the reconcile loop picks up any PENDING signal).
    try:
        from ..execution.policy import decide_execution

        decision = await decide_execution(session, signal)
        log.info(
            "[gate] decide signal=%s mode=%s action=%s%s",
            signal.id,
            decision.mode,
            decision.action,
            f' reason="{decision.reason}"' if decision.reason else "",
        )
    except Exception as exc:
        log.error("[gate] decide_execution failed: %s", exc)

    return GateResult(status="generated", signalId=signal.id, score=ai_result.score)
