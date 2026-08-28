"""Write side of the config layer — port of `apps/api/src/config/store.ts`.

Every mutation validates against the hard bounds, appends a `ConfigAudit` row
(who-changed-what) and busts the resolver cache. Shared by the `/api/config/*`
routes and the Telegram `/mode` `/kill` `/arm` commands so both paths are
audited identically.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ids import new_id
from ...core.serialization import ser
from ...db.enums import ExecutionMode
from ...db.models import ConfigAudit, ExecutionSetting, RiskConfig
from .defaults import RISK_BOUNDS, Scope
from .resolve import bust_config_cache

log = logging.getLogger("backend.config")


@dataclass(slots=True)
class WriteResult:
    ok: bool
    error: str | None = None


def validate_risk_fields(fields: dict[str, Any]) -> str | None:
    """Reject any out-of-bounds or wrongly-typed value. Returns the first error."""
    for key, bound in RISK_BOUNDS.items():
        value = fields.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{key} must be a number"
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return f"{key} must be a number"
        if num < bound.min or num > bound.max:
            return f"{key} must be between {_fmt(bound.min)} and {_fmt(bound.max)}"
        if bound.int and float(value) != int(value):
            return f"{key} must be an integer"
    return None


def _fmt(value: float) -> str:
    """Render a bound the way JS template interpolation did (0.01, 5, 1440)."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _normalize_key(scope: Scope, scope_key: str) -> str:
    return "" if scope == "GLOBAL" else scope_key


def _row_snapshot(row: object | None) -> dict[str, Any]:
    """Serialize a config row for the audit trail (`{}` when the row is new)."""
    if row is None:
        return {}
    payload = {
        k: v for k, v in vars(row).items() if not k.startswith("_")
    }
    return ser(payload)


async def audit(
    session: AsyncSession,
    actor: str,
    entity: str,
    scope: str,
    scope_key: str,
    before: Any,
    after: Any,
) -> None:
    """Append the who-changed-what row. Never raises into the caller."""
    try:
        session.add(
            ConfigAudit(
                id=new_id(),
                actor=actor,
                entity=entity,
                scope=scope,
                scopeKey=scope_key,
                before=before or {},
                after=after or {},
                createdAt=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await session.flush()
    except Exception as exc:  # noqa: BLE001
        log.error("[config] audit write failed: %s", exc)


async def write_risk_config(
    session: AsyncSession,
    actor: str,
    scope: Scope,
    scope_key_raw: str,
    fields: dict[str, Any],
) -> WriteResult:
    """Upsert one RiskConfig scope row, bounded + audited."""
    error = validate_risk_fields(fields)
    if error:
        return WriteResult(False, error)
    scope_key = _normalize_key(scope, scope_key_raw)
    if scope != "GLOBAL" and not scope_key:
        return WriteResult(False, "scopeKey required for non-global scope")

    row = (
        await session.execute(
            select(RiskConfig).where(RiskConfig.scope == scope, RiskConfig.scopeKey == scope_key)
        )
    ).scalar_one_or_none()
    before = _row_snapshot(row)

    updates = {k: fields[k] for k in RISK_BOUNDS if fields.get(k) is not None}
    if fields.get("enabled") is not None:
        updates["enabled"] = bool(fields["enabled"])

    if row is None:
        row = RiskConfig(
            id=new_id(),
            scope=scope,
            scopeKey=scope_key,
            updatedAt=datetime.now(timezone.utc).replace(tzinfo=None),
            **updates,
        )
        session.add(row)
    else:
        for key, value in updates.items():
            setattr(row, key, value)
        row.updatedAt = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.flush()

    await audit(session, actor, "RiskConfig", scope, scope_key, before, _row_snapshot(row))
    await session.commit()
    await bust_config_cache()
    return WriteResult(True)


async def write_execution_mode(
    session: AsyncSession,
    actor: str,
    scope: Scope,
    scope_key_raw: str,
    mode: ExecutionMode | str,
) -> WriteResult:
    """Upsert one ExecutionSetting scope row, audited."""
    scope_key = _normalize_key(scope, scope_key_raw)
    if scope != "GLOBAL" and not scope_key:
        return WriteResult(False, "scopeKey required for non-global scope")
    resolved = ExecutionMode(mode)

    row = (
        await session.execute(
            select(ExecutionSetting).where(
                ExecutionSetting.scope == scope, ExecutionSetting.scopeKey == scope_key
            )
        )
    ).scalar_one_or_none()
    before = _row_snapshot(row)

    if row is None:
        row = ExecutionSetting(
            id=new_id(),
            scope=scope,
            scopeKey=scope_key,
            mode=resolved,
            updatedAt=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(row)
    else:
        row.mode = resolved
        row.updatedAt = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.flush()

    await audit(session, actor, "ExecutionSetting", scope, scope_key, before, _row_snapshot(row))
    await session.commit()
    await bust_config_cache()
    return WriteResult(True)


async def set_kill_switch(session: AsyncSession, actor: str) -> WriteResult:
    """Panic kill-switch: GLOBAL mode = OFF."""
    return await write_execution_mode(session, actor, "GLOBAL", "", ExecutionMode.OFF)


async def arm_system(session: AsyncSession, actor: str) -> WriteResult:
    """Clear a manual kill: GLOBAL mode back to CONFIRM (safe default)."""
    return await write_execution_mode(session, actor, "GLOBAL", "", ExecutionMode.CONFIRM)
