"""Shared async Redis client: cache, pub/sub and distributed locks.

Every caller treats Redis as best-effort. A Redis outage must degrade caching
and realtime without ever bypassing a risk check (plan §9, operational tests),
so the helpers here swallow connection errors and report failure instead of
raising into an execution path.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from ..core.ids import new_id
from ..core.settings import get_settings

log = logging.getLogger("backend.redis")

RT_CHANNEL = "rt"

_client: aioredis.Redis | None = None


def redis_client() -> aioredis.Redis:
    """The process-wide client (lazily created; does not connect eagerly)."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=5.0,
            health_check_interval=30,
        )
    return _client


async def ping_redis(timeout_s: float = 1.5) -> bool:
    """PING with a timeout — used by the readiness probe."""
    try:
        async with asyncio.timeout(timeout_s):
            return bool(await redis_client().ping())
    except Exception as exc:
        log.warning("redis ping failed: %s", exc)
        return False


async def cache_get(key: str) -> str | None:
    try:
        return await redis_client().get(key)
    except Exception as exc:
        log.debug("cache get %s failed: %s", key, exc)
        return None


async def cache_set(key: str, value: str, ttl_s: int | None = None) -> bool:
    try:
        await redis_client().set(key, value, ex=ttl_s)
        return True
    except Exception as exc:
        log.debug("cache set %s failed: %s", key, exc)
        return False


async def cache_del(*keys: str) -> bool:
    if not keys:
        return True
    try:
        await redis_client().delete(*keys)
        return True
    except Exception as exc:
        log.debug("cache del failed: %s", exc)
        return False


async def publish(channel: str, payload: str) -> bool:
    try:
        await redis_client().publish(channel, payload)
        return True
    except Exception as exc:
        log.warning("publish to %s failed: %s", channel, exc)
        return False


@asynccontextmanager
async def try_lock(key: str, ttl_s: int = 60) -> AsyncIterator[bool]:
    """Best-effort singleton lock.

    Yields True when this process holds the lock. On a Redis outage it yields
    True as well: the alternative is silently stopping the scheduled jobs, which
    is worse than a possible double-run of an idempotent tick — and the job
    bodies are written to be idempotent for exactly this reason.
    """
    token = None
    acquired = False
    client = redis_client()
    try:
        token = new_id()
        acquired = bool(await client.set(key, token, nx=True, ex=ttl_s))
        yield acquired
    except Exception as exc:
        log.warning("lock %s unavailable (%s) — proceeding unlocked", key, exc)
        acquired = False
        yield True
    finally:
        if acquired and token is not None:
            try:
                current = await client.get(key)
                if current == token:
                    await client.delete(key)
            except Exception:
                pass


async def close_redis() -> None:
    global _client
    if _client is not None:
        # Shutdown path: nothing is left to report a close failure to.
        with contextlib.suppress(Exception):
            await _client.aclose()
    _client = None
