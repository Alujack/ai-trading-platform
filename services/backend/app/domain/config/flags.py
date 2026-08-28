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
