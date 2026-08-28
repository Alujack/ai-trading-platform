-- Daily profit target: once today's realized P&L reaches this % of balance,
-- the execution breaker holds all new trades until the next UTC day.
ALTER TABLE "RiskConfig" ADD COLUMN "dailyProfitTargetPct" DECIMAL(7,4);
