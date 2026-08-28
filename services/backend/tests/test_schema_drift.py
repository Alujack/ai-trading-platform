"""Schema-drift tripwire.

The SQLAlchemy models in `app/db/models.py` adopt the schema Prisma created —
same tables, columns, types, indexes and foreign keys, down to the referential
actions. That equivalence is what lets both runtimes read and write the same
rows during the migration window, and what lets Alembic take ownership
afterwards without a rewrite.

This test asserts it mechanically: run Alembic's autogenerate comparison against
a migrated database and require an EMPTY diff. It skips when no database is
reachable, so the unit suite stays runnable offline.
"""
from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.settings import get_settings
from app.db.models import Base

# Same scoping as migrations/env.py — Prisma's and Alembic's own bookkeeping
# tables, plus n8n's, are not ours to manage.
IGNORED_TABLES = frozenset({"_prisma_migrations", "alembic_version"})


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    is_foreign_table = type_ == "table" and bool(name) and (
        name in IGNORED_TABLES or name.startswith("n8n")
    )
    return not is_foreign_table


def _compare(connection) -> list:
    """Run Alembic's metadata comparison on a sync-style connection."""
    context = MigrationContext.configure(
        connection, opts={"include_object": _include_object, "compare_type": True}
    )
    return compare_metadata(context, Base.metadata)


async def test_models_match_the_migrated_database():
    """An empty autogenerate diff is the proof that the port is faithful.

    Uses the asyncpg engine the app itself uses — no extra driver dependency —
    and skips when Postgres isn't up so the unit suite stays offline-runnable.
    """
    engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            diff = await connection.run_sync(_compare)
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        pytest.skip(f"database not reachable ({type(exc).__name__}) — skipping drift check")
    finally:
        await engine.dispose()

    assert diff == [], (
        "SQLAlchemy models have drifted from the database schema.\n"
        "Either the models are wrong, or a migration is missing.\n"
        f"Alembic reports: {diff}"
    )


def test_every_prisma_table_has_a_model():
    """A table in the database with no model would be invisible to the backend."""
    expected = {
        "AgentRecommendation",
        "Approval",
        "BacktestRun",
        "BrokerCredential",
        "Candle",
        "ConfigAudit",
        "ExecutionSetting",
        "FeatureFlag",
        "Indicator",
        "Journal",
        "NewsEvent",
        "RawSignal",
        "RiskConfig",
        "RiskLog",
        "Signal",
        "Strategy",
        "Trade",
    }
    assert set(Base.metadata.tables) == expected
