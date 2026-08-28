-- Plan 08, Phase 4: add live broker tracking columns to Trade.
-- externalOrderId: MT5 ticket number (null for paper trades)
-- brokerFillPrice: actual fill price reported by the broker
-- broker: "paper" | "exness_demo" | "exness_real"

ALTER TABLE "Trade" ADD COLUMN "externalOrderId" TEXT;
ALTER TABLE "Trade" ADD COLUMN "brokerFillPrice" DECIMAL(18,8);
ALTER TABLE "Trade" ADD COLUMN "broker" TEXT;

CREATE INDEX "Trade_externalOrderId_idx" ON "Trade"("externalOrderId");
