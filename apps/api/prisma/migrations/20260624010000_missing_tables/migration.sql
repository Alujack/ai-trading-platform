-- Missing tables: RiskConfig, ExecutionSetting, ConfigAudit, BacktestRun, Approval
-- Plus missing Journal columns: grade, outcome, lesson, rMultiple

-- Enums
CREATE TYPE "ExecutionMode" AS ENUM ('OFF', 'AUTO', 'CONFIRM');
CREATE TYPE "ApprovalStatus" AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED');

-- RiskConfig
CREATE TABLE "RiskConfig" (
    "id"                    TEXT NOT NULL,
    "scope"                 TEXT NOT NULL,
    "scopeKey"              TEXT NOT NULL,
    "riskPerTradePct"       DECIMAL(7,4),
    "minRR"                 DECIMAL(7,4),
    "dailyLossLimitPct"     DECIMAL(7,4),
    "maxDrawdownPct"        DECIMAL(7,4),
    "maxOpenTrades"         INTEGER,
    "maxOpenRiskPct"        DECIMAL(7,4),
    "maxRiskPerCurrencyPct" DECIMAL(7,4),
    "newsBeforeMin"         INTEGER,
    "newsAfterMin"          INTEGER,
    "aiMinScore"            INTEGER,
    "approvalTtlMin"        INTEGER,
    "enabled"               BOOLEAN NOT NULL DEFAULT true,
    "updatedAt"             TIMESTAMP(3) NOT NULL,
    CONSTRAINT "RiskConfig_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "RiskConfig_scope_scopeKey_key" ON "RiskConfig"("scope", "scopeKey");

-- ExecutionSetting
CREATE TABLE "ExecutionSetting" (
    "id"        TEXT NOT NULL,
    "scope"     TEXT NOT NULL,
    "scopeKey"  TEXT NOT NULL,
    "mode"      "ExecutionMode" NOT NULL DEFAULT 'CONFIRM',
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "ExecutionSetting_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "ExecutionSetting_scope_scopeKey_key" ON "ExecutionSetting"("scope", "scopeKey");

-- ConfigAudit
CREATE TABLE "ConfigAudit" (
    "id"        TEXT NOT NULL,
    "actor"     TEXT NOT NULL,
    "entity"    TEXT NOT NULL,
    "scope"     TEXT NOT NULL,
    "scopeKey"  TEXT NOT NULL,
    "before"    JSONB NOT NULL,
    "after"     JSONB NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ConfigAudit_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "ConfigAudit_createdAt_idx" ON "ConfigAudit"("createdAt");

-- BacktestRun
CREATE TABLE "BacktestRun" (
    "id"              TEXT NOT NULL,
    "label"           TEXT,
    "startingBalance" DECIMAL(18,2) NOT NULL,
    "riskPct"         DECIMAL(7,4) NOT NULL,
    "costsApplied"    BOOLEAN NOT NULL DEFAULT true,
    "config"          JSONB NOT NULL,
    "results"         JSONB NOT NULL,
    "equityCurves"    JSONB,
    "createdAt"       TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "BacktestRun_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "BacktestRun_createdAt_idx" ON "BacktestRun"("createdAt");

-- Approval
CREATE TABLE "Approval" (
    "id"        TEXT NOT NULL,
    "signalId"  TEXT NOT NULL,
    "status"    "ApprovalStatus" NOT NULL DEFAULT 'PENDING',
    "chatId"    TEXT NOT NULL,
    "messageId" TEXT,
    "decidedBy" TEXT,
    "decidedAt" TIMESTAMP(3),
    "expiresAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "Approval_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "Approval_signalId_key" ON "Approval"("signalId");
CREATE INDEX "Approval_status_idx" ON "Approval"("status");
CREATE INDEX "Approval_expiresAt_idx" ON "Approval"("expiresAt");
ALTER TABLE "Approval" ADD CONSTRAINT "Approval_signalId_fkey"
    FOREIGN KEY ("signalId") REFERENCES "Signal"("id")
    ON DELETE RESTRICT ON UPDATE CASCADE;

-- Journal: add learning-loop columns
ALTER TABLE "Journal"
    ADD COLUMN IF NOT EXISTS "grade"     TEXT,
    ADD COLUMN IF NOT EXISTS "outcome"   TEXT,
    ADD COLUMN IF NOT EXISTS "lesson"    TEXT,
    ADD COLUMN IF NOT EXISTS "rMultiple" DECIMAL(12,4);
