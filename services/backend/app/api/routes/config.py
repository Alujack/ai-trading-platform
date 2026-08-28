"""Runtime risk/execution configuration — port of `routes/config.routes.ts`.

Every mutation goes through `domain.config.store`, so it is bounds-checked,
audited in `ConfigAudit` and cache-busting. `kill` and `arm` are the panic
controls; the raw-feed toggle is visibility-only and changes no execution check.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import select

from ...core.logging import get_logger
from ...core.serialization import js_number, ser
from ...db.enums import ExecutionMode
from ...db.models import RiskConfig
from ...domain.config.defaults import RISK_BOUNDS, bounds_wire
from ...domain.config.flags import (
    LAYER_KEYS,
    RAW_FEED_FLAG,
    get_flag,
    layer_report,
    set_all_layers,
    set_flag,
)
from ...domain.config.resolve import get_execution_map, resolve_risk_config
from ...domain.config.store import (
    arm_system,
    set_kill_switch,
    write_execution_mode,
    write_risk_config,
)
from ..dependencies import Db, ModeParam, ScopeParam

log = get_logger("backend.config")
router = APIRouter(tags=["config"])

ACTOR = "ui:dashboard"


def _risk_field_shape() -> dict[str, Any]:
    """Bounded optional fields, one per numeric risk parameter."""
    shape: dict[str, Any] = {}
    for key, bound in RISK_BOUNDS.items():
        annotation = int if bound.int else float
        shape[key] = (
            annotation | None,
            Field(default=None, ge=bound.min, le=bound.max),
        )
    return shape


PutRiskBody = create_model(  # type: ignore[call-overload]
    "PutRiskBody",
    __config__=ConfigDict(extra="forbid"),
    scope=(ScopeParam, ...),
    scopeKey=(str, Field(default="", max_length=40)),
    enabled=(bool | None, None),
    **_risk_field_shape(),
)


class PutExecBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: ScopeParam
    scopeKey: str = Field(default="", max_length=40)
    mode: ModeParam


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=200)


class RawFeedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class LayersBody(BaseModel):
    """Either flip the whole stack (`mode`) or one layer (`layer` + `enabled`)."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["FULL", "STRATEGY_ONLY"] | None = None
    layer: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None


@router.get("/api/config/risk")
async def get_risk(
    session: Db,
    strategy: str | None = Query(default=None, max_length=40),
    symbol: str | None = Query(default=None, max_length=40),
) -> dict[str, Any]:
    """Resolved effective config for a context, plus every raw row and the bounds."""
    effective = await resolve_risk_config(session, strategy, symbol)
    rows = (
        (
            await session.execute(
                select(RiskConfig).order_by(RiskConfig.scope.asc(), RiskConfig.scopeKey.asc())
            )
        )
        .scalars()
        .all()
    )
    return ser(
        {
            "effective": _effective_wire(effective),
            "rows": [_risk_row(r) for r in rows],
            "bounds": bounds_wire(),
        }
    )


@router.put("/api/config/risk")
async def put_risk(body: PutRiskBody, session: Db, response: Response) -> dict[str, Any]:  # type: ignore[valid-type]
    payload = body.model_dump()  # type: ignore[attr-defined]
    scope = payload.pop("scope")
    scope_key = payload.pop("scopeKey", "") or ""
    result = await write_risk_config(session, ACTOR, scope, scope_key, payload)
    if not result.ok:
        response.status_code = 400
        return {"error": result.error}
    effective = await resolve_risk_config(
        session,
        scope_key if scope == "STRATEGY" else None,
        scope_key if scope == "SYMBOL" else None,
    )
    return ser({"ok": True, "effective": _effective_wire(effective)})


@router.get("/api/config/execution")
async def get_execution(session: Db) -> dict[str, Any]:
    """All execution-mode rows + the effective global."""
    return await get_execution_map(session)


@router.put("/api/config/execution")
async def put_execution(body: PutExecBody, session: Db, response: Response) -> dict[str, Any]:
    result = await write_execution_mode(
        session, ACTOR, body.scope, body.scopeKey, ExecutionMode(body.mode)
    )
    if not result.ok:
        response.status_code = 400
        return {"error": result.error}
    return {"ok": True, **await get_execution_map(session)}


@router.post("/api/config/kill")
async def post_kill(body: ReasonBody, session: Db) -> dict[str, Any]:
    """Panic: set GLOBAL mode = OFF. Signals still generate + log; nothing opens."""
    reason = body.reason or ""
    await set_kill_switch(session, f"{ACTOR}:{reason}" if reason else ACTOR)
    log.warning('[config] KILL switch engaged reason="%s"', reason)
    return {"ok": True, **await get_execution_map(session)}


@router.post("/api/config/arm")
async def post_arm(body: ReasonBody, session: Db) -> dict[str, Any]:
    """Clear a manual kill: GLOBAL mode back to CONFIRM."""
    reason = body.reason or ""
    await arm_system(session, f"{ACTOR}:{reason}" if reason else ACTOR)
    log.warning('[config] system ARMED (CONFIRM) reason="%s"', reason)
    return {"ok": True, **await get_execution_map(session)}


# ---------------------------------------------------------------------------
# Raw strategy feed ("layers off" view)
# ---------------------------------------------------------------------------
# This toggle is VISIBILITY ONLY. On, the gate records every strategy candidate
# untouched and stamps which layer stopped it, so the operator can trade the pure
# strategy signal by hand. It does not disable, relax or reorder a single check on
# the execution path: automation still needs AI + risk approval before a Signal
# exists, and the decider's caps/breakers still stand behind that.


@router.get("/api/config/raw-feed")
async def get_raw_feed(session: Db) -> dict[str, Any]:
    return (await get_flag(session, RAW_FEED_FLAG)).as_dict()


@router.put("/api/config/raw-feed")
async def put_raw_feed(body: RawFeedBody, session: Db) -> dict[str, Any]:
    state = await set_flag(session, ACTOR, RAW_FEED_FLAG, body.enabled)
    log.warning(
        "[config] raw signal feed %s (observe-only)",
        "ENABLED" if body.enabled else "disabled",
    )
    return {"ok": True, **state.as_dict()}


# ---------------------------------------------------------------------------
# Gate layers (switchable)
# ---------------------------------------------------------------------------
# Unlike the raw-feed toggle above, these CHANGE THE EXECUTION PATH. Off, a layer
# stops filtering: no model reviews the setup, or no regime/hour/bias/range check
# applies. `mode=STRATEGY_ONLY` clears all of them at once, so what reaches risk
# is the strategy's raw opinion.
#
# What no switch here can reach — deliberately, per CLAUDE.md: the risk engine
# (sizing, stop/RR validation, news blackout), the breakers, the portfolio caps
# and the data-freshness guard. Every layer defaults ON.


@router.get("/api/config/layers")
async def get_layers(session: Db) -> dict[str, Any]:
    return await layer_report(session)


@router.put("/api/config/layers")
async def put_layers(body: LayersBody, session: Db, response: Response) -> dict[str, Any]:
    if body.mode is not None:
        if body.layer is not None or body.enabled is not None:
            response.status_code = 400
            return {"error": "pass either mode, or layer+enabled — not both"}
        report = await set_all_layers(session, ACTOR, body.mode == "FULL")
        log.warning(
            "[config] gate layers set to %s — %s",
            body.mode,
            "all filters active"
            if body.mode == "FULL"
            else "strategy output only; risk engine, breakers and caps still enforced",
        )
        return {"ok": True, **report}

    if body.layer is None or body.enabled is None:
        response.status_code = 400
        return {"error": "pass mode, or both layer and enabled"}
    if body.layer not in LAYER_KEYS:
        response.status_code = 400
        return {"error": f"unknown layer: {body.layer}"}

    await set_flag(session, ACTOR, body.layer, body.enabled)
    log.warning(
        "[config] gate layer %s %s",
        body.layer,
        "ENABLED" if body.enabled else "DISABLED",
    )
    return {"ok": True, **await layer_report(session)}


def _effective_wire(effective) -> dict[str, Any]:
    """The resolved config with JS-shaped numbers (`1`, not `1.0`)."""
    return {k: js_number(v) for k, v in effective.as_dict().items()}


def _risk_row(r: RiskConfig) -> dict[str, Any]:
    return {
        "id": r.id,
        "scope": r.scope,
        "scopeKey": r.scopeKey,
        **{key: getattr(r, key) for key in RISK_BOUNDS},
        "enabled": r.enabled,
        "updatedAt": r.updatedAt,
    }
