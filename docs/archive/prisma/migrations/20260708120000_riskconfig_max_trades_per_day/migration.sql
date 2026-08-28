-- Per-scope daily trade cap. Until now maxTradesPerDay was a global env default
-- (MAX_TRADES_PER_DAY, 1/day) with no per-strategy override — which starves
-- slow strategies (gold_zigzag_daily, 1/day) the moment a scalper (scalp_sniper,
-- many/day) shares the account. Nullable: absent = inherit next layer / default.
ALTER TABLE "RiskConfig"
  ADD COLUMN IF NOT EXISTS "maxTradesPerDay" INTEGER;
