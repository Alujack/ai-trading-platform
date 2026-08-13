/**
 * Enable the ict_sweep_mss day-trading strategy (XAUUSD, 60min only) — the one
 * ICT detector that passed validation gates on gold.
 *
 * 2026-08-13 re-validation on repaired UTC data (weekend bars filtered, history
 * deepened to 2021+/2024+): 15min is DEAD over 2.5 years (WF-OOS −0.09R, PF 0.85,
 * 297 trades) — its July pass was a one-regime artifact of the short Dec-2025+
 * series. 60min is regime-dependent: flat 2021–2024 (−0.04R), but +0.26R WF-OOS /
 * PF 1.38 fixed-params since Jun 2024 (68 trades, maxDD 9%), reproducing the
 * 2026-07-28 result (+0.23R/PF 1.48). Scope is therefore 60min ONLY; 15min must
 * re-earn its slot through the full Phase 1 gauntlet before being re-added.
 *
 * Idempotent: upserts the Strategy row + a STRATEGY-scoped RiskConfig +
 * ExecutionSetting, so re-running is safe. Run from apps/api:
 *
 *   npx tsx prisma/seedIctSweepMss.ts                      # mode=CONFIRM (safe default)
 *   ICT_SEED_MODE=AUTO npx tsx prisma/seedIctSweepMss.ts   # autonomous (paper broker only!)
 *   ICT_SEED_RISK_PCT=1 ICT_SEED_DAILY_LOSS_PCT=2 ...      # risk knobs (defaults shown)
 *
 * The daily loss cap implements the account rule: stop trading for the day once
 * realized losses reach ICT_SEED_DAILY_LOSS_PCT of balance (enforced by
 * checkDailyLoss in the risk engine via gate.ts's computeTodayLoss).
 *
 * Prerequisites before signals actually flow (see docs/architecture/signal-flow.md):
 *   - XAUUSD candles must be clean UTC (the 2026-07 tz repair) and ingestion running
 *   - STRATEGY_TIMEFRAMES must include 15min,60min (the worker default already does)
 *
 * Reminder (CLAUDE.md): backtest + paper before real money. Seed CONFIRM first,
 * watch a few approvals, and only flip ICT_SEED_MODE=AUTO on the paper broker.
 * Do NOT scope this to 1min/5min: 5min failed validation outright, and the 1min
 * result is pending long-history confirmation.
 */
import { PrismaClient, ExecutionMode } from "@prisma/client";

const prisma = new PrismaClient();

function resolveMode(raw: string | undefined): ExecutionMode {
  const m = (raw ?? "CONFIRM").trim().toUpperCase();
  if (m === "AUTO") return ExecutionMode.AUTO;
  if (m === "OFF") return ExecutionMode.OFF;
  return ExecutionMode.CONFIRM;
}

async function main(): Promise<void> {
  const mode = resolveMode(process.env.ICT_SEED_MODE);
  const riskPct = process.env.ICT_SEED_RISK_PCT ?? "1";
  const dailyLossPct = process.env.ICT_SEED_DAILY_LOSS_PCT ?? "2";

  await prisma.strategy.upsert({
    where: { name: "ict_sweep_mss" },
    update: {
      enabled: true,
      regimes: "TRENDING,RANGING,VOLATILE",
      params: { symbols: ["XAUUSD"], timeframes: ["60min"] },
    },
    create: {
      name: "ict_sweep_mss",
      enabled: true,
      // A reversal trigger: the sweep+shift IS the regime change, so it is never
      // regime-gated (mirrors IctBase.regimes in strategies/ict/_base.py).
      regimes: "TRENDING,RANGING,VOLATILE",
      // Runner-level scoping only; {} strategy knobs = validated code defaults
      // (swingK 2, sweepLookback 5, atrBuffer 0.5, minRr 2.0).
      params: { symbols: ["XAUUSD"], timeframes: ["60min"] },
    },
  });

  // STRATEGY-scoped risk: 1% per trade, hard 2% daily loss stop, RR≥2 (matches the
  // strategy's own target frame), at most one position per timeframe. Nulls fall
  // through to GLOBAL/code defaults. (maxTradesPerDay is in schema.prisma but the
  // generated client predates it — run `prisma generate` and add it if wanted.)
  const risk = {
    riskPerTradePct: riskPct,
    dailyLossLimitPct: dailyLossPct,
    minRR: "2.0",
    maxOpenTrades: 2,
    enabled: true,
  };
  await prisma.riskConfig.upsert({
    where: { scope_scopeKey: { scope: "STRATEGY", scopeKey: "ict_sweep_mss" } },
    update: risk,
    create: { scope: "STRATEGY", scopeKey: "ict_sweep_mss", ...risk },
  });

  await prisma.executionSetting.upsert({
    where: { scope_scopeKey: { scope: "STRATEGY", scopeKey: "ict_sweep_mss" } },
    update: { mode },
    create: { scope: "STRATEGY", scopeKey: "ict_sweep_mss", mode },
  });

  console.log(
    `seeded ict_sweep_mss: XAUUSD 60min, mode=${mode}, ` +
      `risk=${riskPct}%/trade, dailyLossCap=${dailyLossPct}%, maxOpenTrades=2`,
  );
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
