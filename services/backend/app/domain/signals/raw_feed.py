"""The raw ("layers off") strategy feed — port of `signals/rawFeed.ts`.

With the `raw_signal_feed` flag on, every candidate is written here the moment it
reaches the gate — before the AI validator, the risk engine or the regime gate
get a say — and stamped afterwards with whichever layer stopped it. That gives
the operator the untouched strategy view for manual trading while automation
keeps running the full stack unchanged.

This module is observe-only by construction: it writes `RawSignal` rows and
nothing else, `RawSignal` has no relation into `Trade`/`Approval`, and no module
under `domain/execution` imports it. Every function is best-effort — a raw-feed
failure must never break, delay or alter the real gate verdict.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...core.serialization import dec
from ...db.enums import RawVerdict
from ...db.models import RawSignal
from ...jobs.clock import naive_utcnow, utcnow
from ..config.flags import RAW_FEED_FLAG, is_flag_enabled

log = logging.getLogger("backend.rawfeed")

#: Layer tag recorded on a blocked raw candidate (see `RawSignal.blockedBy`).
BLOCKED_BY_TAGS = (
    "duplicate",
    "cooldown",
    "insufficient_candles",
    "ai_unreachable",
    "ai_score",
    "ai_judgment",
    "regime",
    "risk_inputs",
    "risk_daily_loss",
    "risk_drawdown",
    "risk_rr",
    "risk_news",
    "risk_gold",
    "risk",
    "unknown",
)


@dataclass(slots=True)
class GateOutcomeClass:
    verdict: RawVerdict
    blockedBy: str | None


_RISK_INPUTS = re.compile(r"must be|must differ", re.IGNORECASE)
_RISK_DAILY_LOSS = re.compile(r"daily loss limit", re.IGNORECASE)
_RISK_DRAWDOWN = re.compile(r"drawdown", re.IGNORECASE)
_RISK_RR = re.compile(r"risk/reward", re.IGNORECASE)
_RISK_NEWS = re.compile(r"news window", re.IGNORECASE)
_RISK_GOLD = re.compile(r"^risk_rejected:\s*gold|;\s*gold", re.IGNORECASE)


def classify_gate_outcome(status: str, reason: str | None = None) -> GateOutcomeClass:
    """Which layer stopped this candidate?

    Pure, so it is unit-tested against the exact reason strings `gate.py` and
    `risk/engine.py` produce. The risk engine collects ALL its failures into one
    joined reason string, so sub-classification follows `validate_trade`'s own
    push order (inputs → daily loss → drawdown → RR → news → gold) and reports
    the first one it recorded.
    """
    if status == "generated":
        return GateOutcomeClass(RawVerdict.GENERATED, None)
    verdict = RawVerdict.REJECTED if status == "rejected" else RawVerdict.SKIPPED
    r = reason or ""

    def tag() -> str:
        if r.startswith("idempotent_duplicate"):
            return "duplicate"
        if r.startswith("cooldown_active"):
            return "cooldown"
        if r.startswith("insufficient_candles"):
            return "insufficient_candles"
        # Covers both gate spellings: "ai_service_<status>" and
        # "ai_service_unreachable" (the latter is already a prefix match).
        if r.startswith("ai_service_"):
            return "ai_unreachable"
        if r.startswith("ai_score_too_low"):
            return "ai_score"
        if r.startswith("ai_not_approved"):
            return "ai_judgment"
        if r.startswith("pre_gated_regime"):
            return "regime"
        if r.startswith("risk_rejected"):
            if _RISK_INPUTS.search(r):
                return "risk_inputs"
            if _RISK_DAILY_LOSS.search(r):
                return "risk_daily_loss"
            if _RISK_DRAWDOWN.search(r):
                return "risk_drawdown"
            if _RISK_RR.search(r):
                return "risk_rr"
            if _RISK_NEWS.search(r):
                return "risk_news"
            if _RISK_GOLD.search(r):
                return "risk_gold"
            return "risk"
        return "unknown"

    return GateOutcomeClass(verdict, tag())


def dedupe_key_for(candidate, now: datetime | None = None) -> str:
    """Key that collapses re-emissions of the same proposal.

    A strategy carrying a clientId (a per-bar hash) dedupes on that; otherwise we
    key on the levels plus the UTC date, so a 1-minute scan loop re-proposing an
    identical setup bumps one row instead of flooding the feed.
    """
    if getattr(candidate, "clientId", None):
        return f"cid:{candidate.clientId}"
    ref = now or utcnow()
    day = ref.astimezone(ref.tzinfo).strftime("%Y-%m-%d") if ref.tzinfo else ref.strftime("%Y-%m-%d")
    levels = "/".join(
        f"{value:.8f}" if _finite(value) else "nan"
        for value in (candidate.entryPrice, candidate.stopLoss, candidate.takeProfit)
    )
    return (
        f"auto:{day}:{candidate.strategyName}:{candidate.symbol}:"
        f"{candidate.timeframe}:{candidate.direction}:{levels}"
    )


def _finite(value: float) -> bool:
    import math

    return isinstance(value, (int, float)) and math.isfinite(value)


async def raw_feed_enabled(session: AsyncSession) -> bool:
    """Is the raw feed on right now?"""
    return await is_flag_enabled(session, RAW_FEED_FLAG)


async def record_raw_candidate(session: AsyncSession, candidate) -> str | None:
    """Record the untouched candidate and return its `RawSignal` id.

    Returns None when the feed is off (or the write failed — the gate carries on
    regardless).
    """
    if not await raw_feed_enabled(session):
        return None
    key = dedupe_key_for(candidate)
    base = {
        "symbol": candidate.symbol,
        "timeframe": candidate.timeframe,
        "direction": candidate.direction,
        "entryPrice": dec(candidate.entryPrice, 8),
        "stopLoss": dec(candidate.stopLoss, 8),
        "takeProfit": dec(candidate.takeProfit, 8),
        "confidence": round(candidate.confidence),
        "reasoning": candidate.reasoning,
        "strategyName": candidate.strategyName,
    }
    try:
        stmt = (
            pg_insert(RawSignal)
            .values(id=new_id(), dedupeKey=key, lastSeenAt=naive_utcnow(), **base)
            .on_conflict_do_update(
                index_elements=[RawSignal.dedupeKey],
                # A re-emission counts as another sighting and refreshes the
                # levels. The verdict is deliberately NOT reset here:
                # stamp_raw_verdict overwrites it a moment later, and leaving the
                # previous one in place means the row never flickers back to
                # PENDING mid-scan.
                set_={
                    **base,
                    "seenCount": RawSignal.seenCount + 1,
                    "lastSeenAt": naive_utcnow(),
                },
            )
            .returning(RawSignal.id)
        )
        row_id = (await session.execute(stmt)).scalar_one()
        await session.commit()
        return str(row_id)
    except Exception as exc:
        await session.rollback()
        log.error("[rawfeed] record failed: %s", exc)
        return None


#: Layers that only fire BECAUSE an identical candidate already went through. A
#: row that once cleared everything must not be downgraded to one of these by a
#: later re-emission — otherwise the feed reports "duplicate" for a setup the
#: desk actually took.
NON_DOWNGRADING: frozenset[str] = frozenset({"duplicate", "cooldown"})


async def stamp_raw_verdict(session: AsyncSession, raw_id: str | None, result) -> None:
    """Stamp the layer verdict onto a previously recorded raw candidate."""
    if not raw_id:
        return
    outcome = classify_gate_outcome(result.status, result.reason)
    values = {
        "verdict": outcome.verdict,
        "blockedBy": outcome.blockedBy,
        "blockedReason": None if result.status == "generated" else (result.reason or None),
        "signalId": getattr(result, "signalId", None),
    }
    try:
        stmt = sa_update(RawSignal).where(RawSignal.id == raw_id)
        if outcome.blockedBy and outcome.blockedBy in NON_DOWNGRADING:
            # The verdict guard lives in the WHERE clause: a GENERATED row keeps
            # its verdict and signalId, everything else is stamped.
            stmt = stmt.where(RawSignal.verdict != RawVerdict.GENERATED)
        await session.execute(stmt.values(**values))
        await session.commit()
    except Exception as exc:
        await session.rollback()
        log.error("[rawfeed] stamp failed: %s", exc)
