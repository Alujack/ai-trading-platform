"""Async engine, session factory and transaction helpers.

Two entry points, deliberately distinct:

* :func:`session_scope` — a read/write unit of work that commits on success and
  rolls back on any exception. Every domain write goes through it, so a failure
  mid-way can never leave half a trade behind (the graceful-shutdown case in the
  plan's operational test matrix).
* :func:`get_session` — the FastAPI dependency; same guarantees, per-request.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.settings import get_settings

log = logging.getLogger("backend.db")

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    """The process-wide async engine (lazily created)."""
    global _engine
    if _engine is None:
        cfg = get_settings()
        _engine = create_async_engine(
            cfg.sqlalchemy_url,
            pool_size=cfg.db_pool_size,
            max_overflow=cfg.db_max_overflow,
            pool_pre_ping=True,
            echo=cfg.log_level.lower() == "debug",
        )
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(
            engine(), expire_on_commit=False, class_=AsyncSession, autoflush=False
        )
    return _factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional unit of work: commit on success, roll back on error."""
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped transactional session."""
    async with session_scope() as session:
        yield session


async def ping_db(timeout_s: float = 1.5) -> bool:
    """`SELECT 1` with a timeout — used by the readiness probe."""
    try:
        async with asyncio.timeout(timeout_s):
            async with session_factory()() as session:
                await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.warning("db ping failed: %s", exc)
        return False


async def dispose_engine() -> None:
    """Close the pool on shutdown."""
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None
