-- Phase 4.x: intraday pip-based scalping strategy (10-pip target, 1:2 R:R).
INSERT INTO "Strategy" ("id", "name", "enabled", "regimes", "params")
VALUES (
  'strat_scalp_ema',
  'scalp_ema',
  true,
  'TRENDING,RANGING,VOLATILE',
  '{"tpPips":100,"slPips":50,"rsiLongMin":45,"rsiLongMax":68,"rsiShortMin":32,"rsiShortMax":55,"cooldownMs":14400000,"aiMinScore":45,"pip":{"XAUUSD":0.1,"EURUSD":0.0001,"BTCUSD":1.0}}'::jsonb
)
ON CONFLICT ("name") DO NOTHING;
