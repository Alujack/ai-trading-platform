"""Raw strategy feed + runtime feature flags.

Revision ID: 20260828000002
Revises: 20260828000001
Create Date: 2026-08-28

The Alembic equivalent of `apps/api/prisma/migrations/20260828000000_raw_signal_feed`.
That Prisma migration may or may not have been applied yet depending on when a
given environment last ran `prisma migrate deploy`, so every statement here is
idempotent: whichever tool got there first, `alembic upgrade head` converges.

`RawSignal` records every candidate a strategy emits BEFORE any protection layer
runs, then stamps the layer verdict onto it. It is deliberately an island: no FK
to Trade/Approval and no reader under `domain/execution`, so a raw row can never
become a position. Only rows that cleared the whole stack carry a `signalId`, and
execution acts on that Signal, never on this table.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260828000002"
down_revision: str | None = "20260828000001"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE "RawVerdict" AS ENUM ('PENDING', 'GENERATED', 'REJECTED', 'SKIPPED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    if not _has_table("RawSignal"):
        op.execute(
            """
            CREATE TABLE "RawSignal" (
                "id" TEXT NOT NULL,
                "symbol" TEXT NOT NULL,
                "timeframe" TEXT NOT NULL,
                "direction" "Direction" NOT NULL,
                "entryPrice" DECIMAL(18,8) NOT NULL,
                "stopLoss" DECIMAL(18,8) NOT NULL,
                "takeProfit" DECIMAL(18,8) NOT NULL,
                "confidence" INTEGER NOT NULL,
                "reasoning" TEXT NOT NULL,
                "strategyName" TEXT NOT NULL,
                "verdict" "RawVerdict" NOT NULL DEFAULT 'PENDING',
                "blockedBy" TEXT,
                "blockedReason" TEXT,
                "signalId" TEXT,
                "dedupeKey" TEXT NOT NULL,
                "seenCount" INTEGER NOT NULL DEFAULT 1,
                "lastSeenAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT "RawSignal_pkey" PRIMARY KEY ("id")
            );
            """
        )
    op.execute('CREATE UNIQUE INDEX IF NOT EXISTS "RawSignal_dedupeKey_key" ON "RawSignal"("dedupeKey");')
    op.execute('CREATE INDEX IF NOT EXISTS "RawSignal_createdAt_idx" ON "RawSignal"("createdAt");')
    op.execute('CREATE INDEX IF NOT EXISTS "RawSignal_strategyName_idx" ON "RawSignal"("strategyName");')
    op.execute(
        'CREATE INDEX IF NOT EXISTS "RawSignal_symbol_timeframe_idx" ON "RawSignal"("symbol", "timeframe");'
    )
    op.execute('CREATE INDEX IF NOT EXISTS "RawSignal_verdict_idx" ON "RawSignal"("verdict");')

    if not _has_table("FeatureFlag"):
        op.execute(
            """
            CREATE TABLE "FeatureFlag" (
                "key" TEXT NOT NULL,
                "enabled" BOOLEAN NOT NULL DEFAULT false,
                "updatedAt" TIMESTAMP(3) NOT NULL,
                CONSTRAINT "FeatureFlag_pkey" PRIMARY KEY ("key")
            );
            """
        )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "FeatureFlag";')
    op.execute('DROP TABLE IF EXISTS "RawSignal";')
    op.execute('DROP TYPE IF EXISTS "RawVerdict";')
