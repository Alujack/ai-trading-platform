"""Agent-proposal layer for the weekly journal review — port of `execution/reviewAgent.ts`.

The review LLM may propose config changes, but it never applies them: every
proposal is journaled as an `AgentRecommendation` row and delivered to Telegram
for one-tap human approval. Only an approved proposal is written to config —
through the same bounded, audited store functions the UI and Telegram commands
use. The agent's whitelist below is deliberately NARROWER than the human
`RISK_BOUNDS`: the agent can nudge, only the human can floor it.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...db.enums import ApprovalStatus, ExecutionMode
from ...db.models import AgentRecommendation, ConfigAudit, Strategy
from ...integrations.telegram.client import (
    default_chat_id,
    edit_message_text,
    esc,
    is_configured,
    send_message,
)
from ...jobs.clock import as_aware_utc, naive_utcnow, utcnow
from ..config.defaults import Bound, Scope
from ..config.resolve import resolve_execution_mode, resolve_risk_config
from ..config.store import write_execution_mode, write_risk_config

log = logging.getLogger("backend.reviewAgent")

RECOMMENDATION_TTL_H = 72
AGENT_NAME = "weekly_review"

#: Fields the agent may propose changing, with bounds tighter than RISK_BOUNDS.
AGENT_RISK_BOUNDS: dict[str, Bound] = {
    "riskPerTradePct": Bound(0.25, 1.5),
    "minRR": Bound(1.5, 4),
    "dailyLossLimitPct": Bound(0.5, 3),
    "dailyProfitTargetPct": Bound(1, 4),
    "maxOpenTrades": Bound(1, 3, int=True),
    "maxTradesPerDay": Bound(1, 5, int=True),
    "aiMinScore": Bound(50, 90, int=True),
    "newsBeforeMin": Bound(15, 120, int=True),
    "newsAfterMin": Bound(15, 120, int=True),
}

#: The agent may only de-escalate execution: AUTO is never proposable.
AGENT_ALLOWED_MODES: tuple[str, ...] = ("OFF", "CONFIRM")

#: Strategy params the agent may shrink (de-scope), never grow.
AGENT_STRATEGY_FIELDS: tuple[str, ...] = ("symbols", "timeframes")


def _parse_proposed_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw.strip() if isinstance(raw, str) else raw  # lenient: bare CONFIRM


def _strategy_params(params: Any) -> dict[str, Any]:
    return params if isinstance(params, dict) else {}


def _string_array(value: Any) -> list[str] | None:
    """A list of non-empty strings, or None if anything in it isn't one."""
    if not isinstance(value, list):
        return None
    out = [x for x in value if isinstance(x, str) and x.strip() != ""]
    return out if len(out) == len(value) else None


def _fmt_bound(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


async def collect_tunables(session: AsyncSession) -> list[dict[str, Any]]:
    """The tunables the review model is allowed to reason about.

    For every enabled strategy: its effective risk fields, execution mode, and
    scope arrays. This is both the AI-request context and the validation universe.
    """
    strategies = (
        (await session.execute(select(Strategy).where(Strategy.enabled.is_(True))))
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []

    for s in strategies:
        params = _strategy_params(s.params)
        symbols = _string_array(params.get("symbols")) or []
        primary_symbol = symbols[0] if symbols else "XAUUSD"

        eff = await resolve_risk_config(session, s.name, primary_symbol)
        eff_dict = eff.as_dict()
        for field, bound in AGENT_RISK_BOUNDS.items():
            out.append(
                {
                    "entity": "RiskConfig",
                    "scope": "STRATEGY",
                    "scopeKey": s.name,
                    "field": field,
                    "currentValue": eff_dict.get(field),
                    "constraint": (
                        f"{_fmt_bound(bound.min)}–{_fmt_bound(bound.max)}"
                        f"{' (integer)' if bound.int else ''}"
                    ),
                }
            )

        mode = await resolve_execution_mode(session, s.name, primary_symbol)
        out.append(
            {
                "entity": "ExecutionSetting",
                "scope": "STRATEGY",
                "scopeKey": s.name,
                "field": "mode",
                "currentValue": mode.value,
                "constraint": (
                    f"one of {'|'.join(AGENT_ALLOWED_MODES)} — de-escalation only, "
                    "AUTO is not proposable"
                ),
            }
        )

        for field in AGENT_STRATEGY_FIELDS:
            current = _string_array(params.get(field))
            if current and len(current) > 1:
                out.append(
                    {
                        "entity": "Strategy",
                        "scope": "STRATEGY",
                        "scopeKey": s.name,
                        "field": field,
                        "currentValue": current,
                        "constraint": "non-empty strict subset of current (de-scope only)",
                    }
                )
    return out


@dataclass(slots=True)
class Validated:
    entity: str
    scope: Scope
    scopeKey: str
    field: str
    currentValue: Any
    value: Any


async def _validate_proposal(
    session: AsyncSession, proposal: Any
) -> tuple[bool, Validated | str]:
    """Validate one proposal against the agent whitelist and the CURRENT config.

    Used both when the proposal arrives and again at approve-time, so a stale
    approval can't apply against a config that has moved underneath it.
    """
    entity = getattr(proposal, "entity", None) or proposal["entity"]
    scope = getattr(proposal, "scope", None) or proposal["scope"]
    scope_key = getattr(proposal, "scopeKey", None) or proposal.get("scopeKey", "")
    field = getattr(proposal, "field", None) or proposal["field"]
    raw_value = getattr(proposal, "proposedValue", None) or proposal["proposedValue"]

    if scope not in ("STRATEGY", "GLOBAL", "SYMBOL"):
        return False, f"bad scope {scope}"
    if scope != "GLOBAL" and not scope_key:
        return False, "scopeKey required"
    value = _parse_proposed_value(raw_value)

    if entity == "RiskConfig":
        bound = AGENT_RISK_BOUNDS.get(field)
        if bound is None:
            return False, f"field {field} not agent-tunable"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, f"{field} must be a number"
        num = float(value)
        if not math.isfinite(num):
            return False, f"{field} must be a number"
        if num < bound.min or num > bound.max:
            return False, (
                f"{field}={_num_text(value)} outside agent bounds "
                f"{_fmt_bound(bound.min)}–{_fmt_bound(bound.max)}"
            )
        if bound.int and num != int(num):
            return False, f"{field} must be an integer"
        eff = await resolve_risk_config(
            session,
            scope_key if scope == "STRATEGY" else None,
            scope_key if scope == "SYMBOL" else None,
        )
        current = eff.as_dict().get(field)
        if current == num:
            return False, f"{field} already {_num_text(value)}"
        return True, Validated("RiskConfig", scope, scope_key, field, current, num)

    if entity == "ExecutionSetting":
        if field != "mode":
            return False, "only mode is tunable on ExecutionSetting"
        mode = str(value).upper()
        if mode not in AGENT_ALLOWED_MODES:
            return False, (
                f"mode {value} not agent-proposable (only {'|'.join(AGENT_ALLOWED_MODES)})"
            )
        current = await resolve_execution_mode(
            session,
            scope_key if scope == "STRATEGY" else None,
            scope_key if scope == "SYMBOL" else None,
        )
        if current.value == mode:
            return False, f"mode already {mode}"
        return True, Validated(
            "ExecutionSetting", scope, scope_key, "mode", current.value, mode
        )

    if entity == "Strategy":
        if field not in AGENT_STRATEGY_FIELDS:
            return False, f"field {field} not agent-tunable on Strategy"
        if scope != "STRATEGY":
            return False, "Strategy changes must use STRATEGY scope"
        row = (
            await session.execute(select(Strategy).where(Strategy.name == scope_key))
        ).scalar_one_or_none()
        if row is None:
            return False, f"unknown strategy {scope_key}"
        current = _string_array(_strategy_params(row.params).get(field))
        if not current or len(current) < 2:
            return False, f"{field} has no room to de-scope"
        proposed = _string_array(value)
        if not proposed:
            return False, f"{field} must be a non-empty string array"
        is_strict_subset = len(proposed) < len(current) and all(
            x in current for x in proposed
        )
        if not is_strict_subset:
            return False, (
                f"{field} must be a strict subset of [{','.join(current)}] — de-scope only"
            )
        return True, Validated("Strategy", scope, scope_key, field, current, proposed)

    return False, f"unknown entity {entity}"


def _num_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fmt_value(value: Any) -> str:
    return f"[{', '.join(str(v) for v in value)}]" if isinstance(value, list) else str(value)


def _proposal_alert(rec: AgentRecommendation) -> str:
    return "\n".join(
        [
            "🤖 <b>AGENT PROPOSAL</b> — weekly review",
            f"{esc(rec.entity)} · {esc(rec.scope)}:{esc(rec.scopeKey or '(global)')}",
            "",
            f"<b>{esc(rec.field)}</b>: {esc(_fmt_value(rec.currentValue))} → "
            f"<b>{esc(_fmt_value(rec.proposedValue))}</b>",
            "",
            "<b>WHY</b>",
            esc(rec.rationale[:500]),
            "",
            "Approving applies the change through the audited config path. "
            f"Expires in {RECOMMENDATION_TTL_H}h.",
        ]
    )


async def process_review_proposals(
    session: AsyncSession, proposals: list[Any]
) -> dict[str, int]:
    """Journal every valid proposal as PENDING and push the Telegram approval card.

    Invalid proposals are logged and dropped — the agent gets no second opinion.
    Fail-safe like signal approvals: no Telegram means the row is still journaled
    (visible in DB/dashboard), it just can't be approved until the bot is set up.
    """
    created = 0
    rejected = 0
    alerted = 0

    for proposal in proposals[:5]:
        ok, outcome = await _validate_proposal(session, proposal)
        if not ok:
            rejected += 1
            log.warning(
                '[reviewAgent] proposal_rejected entity=%s scope=%s:%s field=%s reason="%s"',
                getattr(proposal, "entity", "?"),
                getattr(proposal, "scope", "?"),
                getattr(proposal, "scopeKey", "?"),
                getattr(proposal, "field", "?"),
                outcome,
            )
            continue
        v = outcome  # type: ignore[assignment]

        dupe = (
            await session.execute(
                select(AgentRecommendation).where(
                    AgentRecommendation.status == ApprovalStatus.PENDING,
                    AgentRecommendation.entity == v.entity,
                    AgentRecommendation.scope == v.scope,
                    AgentRecommendation.scopeKey == v.scopeKey,
                    AgentRecommendation.field == v.field,
                )
            )
        ).scalar_one_or_none()
        if dupe is not None:
            rejected += 1
            log.warning(
                '[reviewAgent] proposal_rejected field=%s reason="pending_duplicate %s"',
                v.field,
                dupe.id,
            )
            continue

        rec = AgentRecommendation(
            id=new_id(),
            agent=AGENT_NAME,
            entity=v.entity,
            scope=v.scope,
            scopeKey=v.scopeKey,
            field=v.field,
            currentValue=v.currentValue,
            proposedValue=v.value,
            rationale=getattr(proposal, "rationale", "") or "",
            status=ApprovalStatus.PENDING,
            chatId=default_chat_id(),
            expiresAt=naive_utcnow() + timedelta(hours=RECOMMENDATION_TTL_H),
            createdAt=naive_utcnow(),
        )
        session.add(rec)
        await session.commit()
        created += 1
        log.info(
            "[reviewAgent] recommendation_created id=%s %s %s:%s %s=%s",
            rec.id,
            v.entity,
            v.scope,
            v.scopeKey,
            v.field,
            _fmt_value(v.value),
        )

        if not is_configured() or not rec.chatId:
            log.warning(
                "[reviewAgent] telegram_not_configured — recommendation %s journaled without alert",
                rec.id,
            )
            continue
        message_id = await send_message(
            rec.chatId,
            _proposal_alert(rec),
            [
                [
                    {"text": "✅ Approve", "callback_data": f"rca:{rec.id}"},
                    {"text": "❌ Reject", "callback_data": f"rcr:{rec.id}"},
                ]
            ],
        )
        if message_id:
            rec.messageId = message_id
            await session.commit()
            alerted += 1

    return {"created": created, "rejected": rejected, "alerted": alerted}


async def apply_recommendation_decision(
    session: AsyncSession, recommendation_id: str, approve: bool, decided_by: str
) -> dict[str, Any]:
    """Apply an Approve/Reject decision for an agent recommendation. Idempotent.

    Approval re-validates against the live config, then writes through the audited
    store path with an actor string that names both the human and the agent.
    """
    rec = (
        await session.execute(
            select(AgentRecommendation).where(AgentRecommendation.id == recommendation_id)
        )
    ).scalar_one_or_none()
    if rec is None:
        return {"ok": False, "outcome": "not_found", "message": "Recommendation not found."}
    if rec.status != ApprovalStatus.PENDING:
        return {
            "ok": False,
            "outcome": "already_decided",
            "message": f"Already {rec.status.value.lower()}.",
        }
    if as_aware_utc(rec.expiresAt) < utcnow():
        rec.status = ApprovalStatus.EXPIRED
        await session.commit()
        return {
            "ok": False,
            "outcome": "expired",
            "message": "This recommendation already expired.",
        }

    stamp = naive_utcnow()
    if not approve:
        rec.status = ApprovalStatus.REJECTED
        rec.decidedBy = decided_by
        rec.decidedAt = stamp
        await session.commit()
        return {"ok": True, "outcome": "rejected", "message": f"❌ Rejected by {decided_by}"}

    # Re-validate against the config as it is NOW, not as it was at proposal time.
    ok, outcome = await _validate_proposal(
        session,
        {
            "entity": rec.entity,
            "scope": rec.scope,
            "scopeKey": rec.scopeKey,
            "field": rec.field,
            "proposedValue": json.dumps(rec.proposedValue),
            "rationale": rec.rationale,
        },
    )
    if not ok:
        return {
            "ok": False,
            "outcome": "apply_failed",
            "message": f"Could not apply: {outcome}",
        }
    v = outcome  # type: ignore[assignment]
    actor = f"{decided_by} via {AGENT_NAME}"

    if v.entity == "RiskConfig":
        applied = await write_risk_config(session, actor, v.scope, v.scopeKey, {v.field: v.value})
    elif v.entity == "ExecutionSetting":
        applied = await write_execution_mode(
            session, actor, v.scope, v.scopeKey, ExecutionMode(v.value)
        )
    else:
        applied = await _apply_strategy_descope(session, actor, v.scopeKey, v.field, v.value)
    if not applied.ok:
        return {
            "ok": False,
            "outcome": "apply_failed",
            "message": f"Could not apply: {applied.error}",
        }

    rec.status = ApprovalStatus.APPROVED
    rec.decidedBy = decided_by
    rec.decidedAt = stamp
    rec.appliedAt = stamp
    await session.commit()
    return {
        "ok": True,
        "outcome": "approved",
        "message": f"✅ Approved by {decided_by} · change applied",
    }


async def _apply_strategy_descope(
    session: AsyncSession, actor: str, strategy_name: str, field: str, proposed: list[str]
):
    """De-scope a strategy's symbols/timeframes params, with a `ConfigAudit` entry."""
    from ..config.store import WriteResult

    row = (
        await session.execute(select(Strategy).where(Strategy.name == strategy_name))
    ).scalar_one_or_none()
    if row is None:
        return WriteResult(False, f"unknown strategy {strategy_name}")
    before = _strategy_params(row.params)
    after = {**before, field: proposed}
    row.params = after
    session.add(
        ConfigAudit(
            id=new_id(),
            actor=actor,
            entity="Strategy",
            scope="STRATEGY",
            scopeKey=strategy_name,
            before=before,
            after=after,
            createdAt=naive_utcnow(),
        )
    )
    await session.commit()
    return WriteResult(True)


async def expire_stale_recommendations(session: AsyncSession) -> dict[str, int]:
    """Expire PENDING recommendations past their TTL and stamp the Telegram card."""
    stale = (
        (
            await session.execute(
                select(AgentRecommendation)
                .where(
                    AgentRecommendation.status == ApprovalStatus.PENDING,
                    AgentRecommendation.expiresAt < naive_utcnow(),
                )
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    expired = 0
    for rec in stale:
        rec.status = ApprovalStatus.EXPIRED
        await session.commit()
        if rec.chatId and rec.messageId:
            await edit_message_text(
                rec.chatId,
                rec.messageId,
                "⌛ <b>Expired</b> — recommendation not acted on.",
            )
        expired += 1
    return {"expired": expired}
