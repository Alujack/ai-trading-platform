"""Baseline: adopt the schema Prisma created.

Revision ID: 20260828000001
Revises:
Create Date: 2026-08-28

This revision is an ADOPTION, not a creation. On the existing database the
tables, enums and indexes were all created by
`apps/api/prisma/migrations/*` — stamping this revision records "the schema is
current" without touching a single production row, which is the Phase 1 exit
gate ("do not recreate production tables").

On a *fresh* database (a CI fixture, a new environment) the same revision builds
the schema from the SQLAlchemy models, so `alembic upgrade head` is the single
way to get a working database either way. The existence of `"Candle"` is the
discriminator.

The Prisma SQL history stays in the repo as the archive of how the schema got
here; Alembic owns everything from this point forward.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db.models import Base

revision: str = "20260828000001"
down_revision: str | None = None
branch_labels = None
depends_on = None

#: The Postgres enum types the Prisma migrations created.
_ENUMS: dict[str, tuple[str, ...]] = {
    "Impact": ("LOW", "MEDIUM", "HIGH"),
    "Direction": ("LONG", "SHORT"),
    "SignalStatus": ("PENDING", "ACTIVE", "CLOSED", "CANCELLED"),
    "TradeStatus": ("OPEN", "CLOSED"),
    "ExecutionMode": ("OFF", "AUTO", "CONFIRM"),
    "ApprovalStatus": ("PENDING", "APPROVED", "REJECTED", "EXPIRED"),
    "RawVerdict": ("PENDING", "GENERATED", "REJECTED", "SKIPPED"),
}


def _schema_already_exists() -> bool:
    inspector = sa.inspect(op.get_bind())
    return "Candle" in inspector.get_table_names()


def upgrade() -> None:
    if _schema_already_exists():
        # Prisma already owns these objects — adopt them untouched.
        return

    bind = op.get_bind()
    for name, values in _ENUMS.items():
        sa.Enum(*values, name=name).create(bind, checkfirst=True)
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Refusing to drop the trading schema is deliberate: an accidental
    # `alembic downgrade base` must not be able to destroy the trade history.
    raise RuntimeError(
        "The baseline revision is not reversible — it adopts a pre-existing schema. "
        "Restore from a backup instead of downgrading."
    )
