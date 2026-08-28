-- Phase 4: unify the strategy framework.

-- CreateTable
CREATE TABLE "Strategy" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "enabled" BOOLEAN NOT NULL DEFAULT false,
    "regimes" TEXT NOT NULL,
    "params" JSONB NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Strategy_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "Strategy_name_key" ON "Strategy"("name");

-- AlterTable: tag each signal with the strategy that produced it.
ALTER TABLE "Signal" ADD COLUMN "strategyName" TEXT;

-- CreateIndex
CREATE INDEX "Signal_strategyName_idx" ON "Signal"("strategyName");

-- Seed the two legacy strategies, parameters preserved from their current code.
INSERT INTO "Strategy" ("id", "name", "enabled", "regimes", "params")
VALUES
  (
    'strat_trend_ema',
    'trend_ema',
    true,
    'TRENDING',
    '{"emaFast":20,"emaSlow":50,"rsiMin":40,"rsiMax":55,"atrMin":5,"atrStopMult":1.5,"atrTargetMult":3,"cooldownMs":3600000,"aiMinScore":70,"longOnly":true}'::jsonb
  ),
  (
    'strat_meanrev_rsi',
    'meanrev_rsi',
    true,
    'RANGING',
    '{"rsiOversold":30,"rsiOverbought":70,"atrStopMult":1.5,"atrTargetMult":3,"aiMinScore":70}'::jsonb
  )
ON CONFLICT ("name") DO NOTHING;
