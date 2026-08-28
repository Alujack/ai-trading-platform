-- Raw strategy feed ("layers off" view) + runtime feature flags.
--
-- RawSignal records every candidate a strategy emits BEFORE any protection
-- layer runs, then stamps the layer verdict onto it. It is deliberately an
-- island: no FK to Trade/Approval and no reader in execution/, so a raw row can
-- never become a position. Only rows that cleared the whole stack carry a
-- signalId, and execution acts on that Signal, never on this table.
CREATE TYPE "RawVerdict" AS ENUM ('PENDING', 'GENERATED', 'REJECTED', 'SKIPPED');

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

CREATE UNIQUE INDEX "RawSignal_dedupeKey_key" ON "RawSignal"("dedupeKey");
CREATE INDEX "RawSignal_createdAt_idx" ON "RawSignal"("createdAt");
CREATE INDEX "RawSignal_strategyName_idx" ON "RawSignal"("strategyName");
CREATE INDEX "RawSignal_symbol_timeframe_idx" ON "RawSignal"("symbol", "timeframe");
CREATE INDEX "RawSignal_verdict_idx" ON "RawSignal"("verdict");

-- Runtime on/off switches the dashboard can flip (today: raw_signal_feed).
CREATE TABLE "FeatureFlag" (
    "key" TEXT NOT NULL,
    "enabled" BOOLEAN NOT NULL DEFAULT false,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "FeatureFlag_pkey" PRIMARY KEY ("key")
);
