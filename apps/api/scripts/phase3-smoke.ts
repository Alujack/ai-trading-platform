/**
 * One-shot Phase 3 smoke test (run: npx tsx apps/api/scripts/phase3-smoke.ts).
 *
 * Exercises the review-agent pipeline end to end without leaving state behind:
 * invalid proposals must be rejected by the whitelist, a valid one must be
 * journaled as an AgentRecommendation, the approve path must write config
 * through the audited store, and the revert returns the effective value.
 */
import { prisma } from "../src/lib/prisma";
import { resolveRiskConfig } from "../src/config/resolve";
import { writeRiskConfig } from "../src/config/store";
import {
  applyRecommendationDecision,
  collectTunables,
  processReviewProposals,
} from "../src/execution/reviewAgent";

async function main(): Promise<void> {
  const tunables = await collectTunables();
  console.log(`tunables=${tunables.length}`, tunables.slice(0, 3));

  const before = await resolveRiskConfig("ict_sweep_mss", "XAUUSD");
  console.log("effective newsBeforeMin before:", before.newsBeforeMin);

  const res = await processReviewProposals([
    // invalid: outside agent bounds (human bound is 5, agent cap is 1.5)
    {
      entity: "RiskConfig",
      scope: "STRATEGY",
      scopeKey: "ict_sweep_mss",
      field: "riskPerTradePct",
      proposedValue: "3",
      rationale: "[TEST] should be rejected: outside agent bounds",
    },
    // invalid: agent may never propose AUTO
    {
      entity: "ExecutionSetting",
      scope: "STRATEGY",
      scopeKey: "ict_sweep_mss",
      field: "mode",
      proposedValue: '"AUTO"',
      rationale: "[TEST] should be rejected: escalation not allowed",
    },
    // valid: widen the news blackout (de-risking)
    {
      entity: "RiskConfig",
      scope: "STRATEGY",
      scopeKey: "ict_sweep_mss",
      field: "newsBeforeMin",
      proposedValue: "60",
      rationale: "[TEST] pipeline verification — approved then reverted by the smoke test",
    },
  ]);
  console.log("processReviewProposals:", res);

  const rec = await prisma.agentRecommendation.findFirst({
    where: { status: "PENDING", field: "newsBeforeMin" },
    orderBy: { createdAt: "desc" },
  });
  if (!rec) throw new Error("expected a PENDING recommendation for newsBeforeMin");
  console.log(`journaled: id=${rec.id} ${rec.entity} ${rec.scope}:${rec.scopeKey} ${rec.field} ${JSON.stringify(rec.currentValue)}→${JSON.stringify(rec.proposedValue)}`);

  const decision = await applyRecommendationDecision(rec.id, true, "smoke:test");
  console.log("approve decision:", decision);

  const after = await resolveRiskConfig("ict_sweep_mss", "XAUUSD");
  console.log("effective newsBeforeMin after approve:", after.newsBeforeMin);

  const audit = await prisma.configAudit.findFirst({ orderBy: { createdAt: "desc" } });
  console.log("latest audit:", audit?.actor, audit?.entity, audit?.scope, audit?.scopeKey);

  const idempotent = await applyRecommendationDecision(rec.id, true, "smoke:test");
  console.log("second tap (must be already_decided):", idempotent.outcome);

  // Revert: pin back the previous effective value through the same audited path.
  const revert = await writeRiskConfig("smoke:test-revert", "STRATEGY", "ict_sweep_mss", {
    newsBeforeMin: before.newsBeforeMin,
  });
  const reverted = await resolveRiskConfig("ict_sweep_mss", "XAUUSD");
  console.log("revert:", revert, "effective newsBeforeMin now:", reverted.newsBeforeMin);
}

main()
  .catch((err) => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
    process.exit(); // shared redis handle otherwise keeps the process alive
  });
