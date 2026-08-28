"""Runtime feature flags — port of `apps/api/src/config/flags.ts`.

Same pattern as the risk config: Redis-cached DB rows, busted on write, audited
in `ConfigAudit`. Resolution is DB row ► env default ► false, so a flag is off
until someone sets the env var or flips it in the UI, and the UI always wins.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.settings import get_settings
from ...db.models import FeatureFlag
from ...db.redis_client import cache_del, cache_get, cache_set
from .store import audit

log = logging.getLogger("backend.flags")

#: Record every raw strategy candidate (protection layers OFF, observe-only).
RAW_FEED_FLAG = "raw_signal_feed"

#: --- Discretionary gate layers -------------------------------------------- #
#: The filters between "a strategy saw a setup" and "a signal exists". Each is
#: switchable so the desk can run pure strategy output, and each DEFAULTS ON
#: (see `_env_default`): every other flag in this module *adds* a feature and is
#: safe to resolve False, but these REMOVE a check, so absence of configuration
#: and any resolution failure must both leave them in place.
#:
#: What is NOT here is the point: position sizing, stop/RR validation, the news
#: blackout, the daily-loss and drawdown breakers, the portfolio caps and the
#: data-freshness guard are mandatory (CLAUDE.md). "Strategy only" means no
#: opinion is applied to the setup, not that trades stop being sized and bounded.
#:
#: Each flag key doubles as the Settings field name holding its env default.
AI_VALIDATION_FLAG = "ai_validation"
REGIME_GATING_FLAG = "regime_gating"
KILLZONE_GATING_FLAG = "killzone_gating"
BIAS_FILTER_FLAG = "bias_filter"
DISCOUNT_FILTER_FLAG = "discount_filter"


@dataclass(frozen=True, slots=True)
class LayerSpec:
    """One switchable layer: its flag, where it runs, and what off means."""

    key: str
    label: str
    #: Which runtime applies it — "gate" (FastAPI) or "strategy" (data worker).
    applied_by: Literal["gate", "strategy"]
    #: Strategy constructor param the worker overrides when this is off, if any.
    param: str | None
    off_means: str


#: Order is display order: outermost filter first.
LAYERS: tuple[LayerSpec, ...] = (
    LayerSpec(
        key=AI_VALIDATION_FLAG,
        label="AI validation",
        applied_by="gate",
        param=None,
        off_means="No model scores or vetoes the setup; strategy reasoning is the rationale.",
    ),
    LayerSpec(
        key=REGIME_GATING_FLAG,
        label="Regime gating",
        applied_by="strategy",
        param=None,
        off_means="Strategies run in every regime, including the chop they declared they avoid.",
    ),
    LayerSpec(
        key=KILLZONE_GATING_FLAG,
        label="Killzone hours",
        applied_by="strategy",
        param="useKillzone",
        off_means="Setups are taken at any hour, not only the London / NY-AM windows.",
    ),
    LayerSpec(
        key=BIAS_FILTER_FLAG,
        label="Trend bias",
        applied_by="strategy",
        param="useBias",
        off_means="Counter-trend setups are allowed; the EMA bias no longer vetoes a direction.",
    ),
    LayerSpec(
        key=DISCOUNT_FILTER_FLAG,
        label="Premium / discount",
        applied_by="strategy",
        param="requireDiscount",
        off_means="Longs may be taken in premium and shorts in discount.",
    ),
)

LAYER_KEYS: tuple[str, ...] = tuple(layer.key for layer in LAYERS)

#: Checks no switch can reach, carried alongside the layers so the split between
#: "opinion" and "protection" stays legible wherever they are rendered.
MANDATORY_LAYERS: tuple[str, ...] = (
    "Risk engine — position sizing, stop/RR validation, news blackout",
    "Breakers — daily loss, drawdown, kill switch",
    "Portfolio caps — max open trades, per-currency exposure",
    "Data freshness — a stale series is never traded",
)

FLAG_CACHE_KEY = "config:flags:rows"
CACHE_TTL_S = 300  # safety net; writes bust the cache explicitly

FlagSource = Literal["db", "env", "default"]


@dataclass(slots=True)
class FlagState:
    key: str
    enabled: bool
    source: FlagSource

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "enabled": self.enabled, "source": self.source}


def _env_default(key: str) -> bool | None:
    """The env var consulted when a flag has no DB row yet."""
    if key == RAW_FEED_FLAG:
        return get_settings().raw_signal_feed
    if key in LAYER_KEYS:
        # Layers are ON until told otherwise, so `is_flag_enabled`'s except-path
        # (`_env_default(key) or False`) also keeps them in place through a
        # Redis/DB wobble rather than silently dropping a check.
        configured = getattr(get_settings(), key, None)
        return True if configured is None else configured
    return None


async def _flag_rows(session: AsyncSession) -> list[dict[str, object]]:
    cached = await cache_get(FLAG_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            pass
    rows = (await session.execute(select(FeatureFlag.key, FeatureFlag.enabled))).all()
    payload = [{"key": r[0], "enabled": r[1]} for r in rows]
    await cache_set(FLAG_CACHE_KEY, json.dumps(payload), CACHE_TTL_S)
    return payload


async def bust_flag_cache() -> None:
    """Invalidate the flag cache — call after any flag write."""
    await cache_del(FLAG_CACHE_KEY)


async def get_flag(session: AsyncSession, key: str) -> FlagState:
    for row in await _flag_rows(session):
        if row["key"] == key:
            return FlagState(key, bool(row["enabled"]), "db")
    env = _env_default(key)
    if env is not None:
        return FlagState(key, env, "env")
    return FlagState(key, False, "default")


async def is_flag_enabled(session: AsyncSession, key: str) -> bool:
    """Is a flag on? Never raises — an outage can only turn a feature OFF."""
    try:
        return (await get_flag(session, key)).enabled
    except Exception as exc:
        log.error("[flags] resolve failed: %s", exc)
        return _env_default(key) or False


async def set_flag(session: AsyncSession, actor: str, key: str, enabled: bool) -> FlagState:
    """Upsert a flag, audit it, and bust the cache."""
    row = (
        await session.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    ).scalar_one_or_none()
    before = {} if row is None else {"key": row.key, "enabled": row.enabled}

    if row is None:
        row = FeatureFlag(
            key=key, enabled=enabled, updatedAt=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        session.add(row)
    else:
        row.enabled = enabled
        row.updatedAt = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.flush()

    await audit(
        session,
        actor,
        "FeatureFlag",
        "GLOBAL",
        key,
        before,
        {"key": row.key, "enabled": row.enabled},
    )
    await session.commit()
    await bust_flag_cache()
    return FlagState(key, row.enabled, "db")

async def layer_states(session: AsyncSession) -> dict[str, bool]:
    """Every switchable layer resolved in one pass, keyed by flag.

    One `_flag_rows` read serves all of them, so a scan or a page load costs a
    single query rather than one per layer.
    """
    rows = {row["key"]: bool(row["enabled"]) for row in await _flag_rows(session)}
    out: dict[str, bool] = {}
    for key in LAYER_KEYS:
        if key in rows:
            out[key] = rows[key]
        else:
            env = _env_default(key)
            out[key] = True if env is None else env
    return out


async def layer_report(session: AsyncSession) -> dict[str, object]:
    """Layer states plus the mode they add up to, for the API and the dashboard."""
    try:
        states = await layer_states(session)
    except Exception as exc:  # noqa: BLE001 — never fail a page load over this
        log.error("[flags] layer resolve failed: %s", exc)
        states = {key: True for key in LAYER_KEYS}

    enabled = [key for key in LAYER_KEYS if states[key]]
    if len(enabled) == len(LAYER_KEYS):
        mode = "FULL"
    elif not enabled:
        mode = "STRATEGY_ONLY"
    else:
        mode = "MIXED"
    return {
        "mode": mode,
        "layers": [
            {
                "key": layer.key,
                "label": layer.label,
                "enabled": states[layer.key],
                "appliedBy": layer.applied_by,
                # The strategy-constructor param the data worker overrides when
                # this layer is off. Exposed so the mapping lives here only and
                # the worker stays a generic consumer of it.
                "param": layer.param,
                "offMeans": layer.off_means,
            }
            for layer in LAYERS
        ],
        "mandatory": list(MANDATORY_LAYERS),
    }


async def set_all_layers(session: AsyncSession, actor: str, enabled: bool) -> dict[str, object]:
    """Flip every layer at once — the "full stack" / "strategy only" switch.

    Each layer is written through `set_flag`, so the change lands as one audit row
    per layer rather than one opaque bulk entry: the log still answers "who turned
    the killzone filter off, and when".
    """
    for key in LAYER_KEYS:
        await set_flag(session, actor, key, enabled)
    return await layer_report(session)
