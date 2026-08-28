-- Add the Bollinger-band + ADX indicator columns introduced in the data
-- pipeline (commit 9f10813) but never migrated. All nullable — historical
-- Indicator rows simply have no band/ADX values until re-computed.
ALTER TABLE "Indicator"
  ADD COLUMN IF NOT EXISTS "bbLower" DECIMAL(18,8),
  ADD COLUMN IF NOT EXISTS "bbUpper" DECIMAL(18,8),
  ADD COLUMN IF NOT EXISTS "bbPctB" DECIMAL(18,8),
  ADD COLUMN IF NOT EXISTS "adx" DECIMAL(18,8);
