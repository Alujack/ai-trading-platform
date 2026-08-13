/**
 * One-shot config repair (plan 10 Phase 3): re-arm the pre-news blackout.
 *
 * - SYMBOL XAUUSD had newsBeforeMin=0 (legacy), silently disabling the
 *   ±30m high-impact-news block the plan expects → set to 30 (audited).
 * - The phase3 smoke test pinned newsBeforeMin=0 on the STRATEGY row that
 *   previously inherited → restore NULL (audited).
 */
import { prisma } from "../src/lib/prisma";
import { bustConfigCache, resolveRiskConfig } from "../src/config/resolve";

async function main(): Promise<void> {
  const symBefore = await prisma.riskConfig.findUnique({
    where: { scope_scopeKey: { scope: "SYMBOL", scopeKey: "XAUUSD" } },
  });
  const symAfter = await prisma.riskConfig.update({
    where: { scope_scopeKey: { scope: "SYMBOL", scopeKey: "XAUUSD" } },
    data: { newsBeforeMin: 30 },
  });
  await prisma.configAudit.create({
    data: {
      actor: "system:plan10-phase3",
      entity: "RiskConfig",
      scope: "SYMBOL",
      scopeKey: "XAUUSD",
      before: JSON.parse(JSON.stringify(symBefore)),
      after: JSON.parse(JSON.stringify(symAfter)),
    },
  });

  const stratBefore = await prisma.riskConfig.findUnique({
    where: { scope_scopeKey: { scope: "STRATEGY", scopeKey: "ict_sweep_mss" } },
  });
  const stratAfter = await prisma.riskConfig.update({
    where: { scope_scopeKey: { scope: "STRATEGY", scopeKey: "ict_sweep_mss" } },
    data: { newsBeforeMin: null },
  });
  await prisma.configAudit.create({
    data: {
      actor: "system:plan10-phase3",
      entity: "RiskConfig",
      scope: "STRATEGY",
      scopeKey: "ict_sweep_mss",
      before: JSON.parse(JSON.stringify(stratBefore)),
      after: JSON.parse(JSON.stringify(stratAfter)),
    },
  });

  await bustConfigCache();
  const eff = await resolveRiskConfig("ict_sweep_mss", "XAUUSD");
  console.log(`effective news window now: -${eff.newsBeforeMin}m / +${eff.newsAfterMin}m`);
}

main()
  .catch((err) => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
    process.exit();
  });
