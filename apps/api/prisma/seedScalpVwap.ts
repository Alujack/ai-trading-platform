/**
 * Phase 4 — enable the scalp_vwap autonomous scalper. Idempotent: upserts the
 * Strategy row + a STRATEGY-scoped RiskConfig + ExecutionSetting, so re-running is
 * safe. Run from apps/api:
 *
 *   npx tsx prisma/seedScalpVwap.ts                       # mode=CONFIRM (safe default)
 *   SCALP_SEED_MODE=AUTO npx tsx prisma/seedScalpVwap.ts  # fully autonomous (after a green backtest!)
 *   SCALP_SEED_RISK_PCT=1.5 ... (default 1)               # risk per trade, %
 *
 * This only registers the strategy + its risk/exec policy. To actually trade it you
 * ALSO need, via env (see .env.example):
 *   - STRATEGY_TIMEFRAMES to include 1min (and/or 5min) and STRATEGY_SYMBOLS your symbols
 *   - BROKER=exness + the MT5 bridge up, for live; live order size uses PAPER_RISK_PERCENT
 *   - ENABLE_SCALP_MANAGER=true to arm the 15s active-management loop
 *
 * Reminder (CLAUDE.md): backtest + paper before real money. Seed CONFIRM first,
 * watch a few approvals, and only flip SCALP_SEED_MODE=AUTO once you trust it.
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
  const mode = resolveMode(process.env.SCALP_SEED_MODE);
  const riskPct = process.env.SCALP_SEED_RISK_PCT ?? "1"; // conservative 1% (1–2% band)

  await prisma.strategy.upsert({
    where: { name: "scalp_vwap" },
    update: { enabled: true, regimes: "TRENDING" },
    create: {
      name: "scalp_vwap",
      enabled: true,
      regimes: "TRENDING", // gated OUT of RANGING (chop) + VOLATILE (news) by the runner
      params: {}, // {} → strategy code defaults (see strategies/scalp_vwap.py)
    },
  });

  // STRATEGY-scoped risk: conservative size, RR 2.0 (matches the strategy's ATR frame),
  // one scalp at a time, AI gate at 60. Nulls fall through to GLOBAL/code defaults.
  await prisma.riskConfig.upsert({
    where: { scope_scopeKey: { scope: "STRATEGY", scopeKey: "scalp_vwap" } },
    update: { riskPerTradePct: riskPct, minRR: "2.0", maxOpenTrades: 1, aiMinScore: 60, enabled: true },
    create: {
      scope: "STRATEGY",
      scopeKey: "scalp_vwap",
      riskPerTradePct: riskPct,
      minRR: "2.0",
      maxOpenTrades: 1,
      aiMinScore: 60,
      enabled: true,
    },
  });

  await prisma.executionSetting.upsert({
    where: { scope_scopeKey: { scope: "STRATEGY", scopeKey: "scalp_vwap" } },
    update: { mode },
    create: { scope: "STRATEGY", scopeKey: "scalp_vwap", mode },
  });

  console.log(
    `[seedScalpVwap] scalp_vwap enabled — regimes=TRENDING risk=${riskPct}% minRR=2.0 ` +
      `maxOpenTrades=1 aiMinScore=60 mode=${mode}. ` +
      `Reminder: set STRATEGY_TIMEFRAMES (1min/5min) + ENABLE_SCALP_MANAGER=true; backtest before live.`,
  );
}

main()
  .catch((err) => {
    console.error("[seedScalpVwap] failed:", err);
    process.exitCode = 1;
  })
  .finally(() => void prisma.$disconnect());
