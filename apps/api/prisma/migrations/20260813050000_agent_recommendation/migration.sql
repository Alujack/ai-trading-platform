-- Agent-proposed config changes (plan 10 Phase 3): journaled recommendations
-- applied only after human approval. Table was first created via `prisma db
-- push` on 2026-08-13; this migration is the durable record for fresh
-- environments and was marked applied via `prisma migrate resolve` on the dev DB.
CREATE TABLE "AgentRecommendation" (
    "id" TEXT NOT NULL,
    "agent" TEXT NOT NULL,
    "entity" TEXT NOT NULL,
    "scope" TEXT NOT NULL,
    "scopeKey" TEXT NOT NULL,
    "field" TEXT NOT NULL,
    "currentValue" JSONB NOT NULL,
    "proposedValue" JSONB NOT NULL,
    "rationale" TEXT NOT NULL,
    "status" "ApprovalStatus" NOT NULL DEFAULT 'PENDING',
    "chatId" TEXT,
    "messageId" TEXT,
    "decidedBy" TEXT,
    "decidedAt" TIMESTAMP(3),
    "appliedAt" TIMESTAMP(3),
    "expiresAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "AgentRecommendation_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "AgentRecommendation_status_idx" ON "AgentRecommendation"("status");

CREATE INDEX "AgentRecommendation_expiresAt_idx" ON "AgentRecommendation"("expiresAt");

CREATE INDEX "AgentRecommendation_createdAt_idx" ON "AgentRecommendation"("createdAt");
