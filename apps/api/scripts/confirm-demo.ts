/**
 * End-to-end CONFIRM-loop demo — runs the REAL execution code without needing a
 * live Telegram bot. It:
 *   1. routes a signal through the decider in CONFIRM mode (→ creates an Approval)
 *   2. prints the exact alert card your phone will show (formatAlert)
 *   3. approves it through the same path the webhook uses (→ opens a paper trade)
 * Everything is created under a throwaway DEMOUSD symbol and cleaned up at the end.
 *
 *   DATABASE_URL=... REDIS_URL=... npx tsx scripts/confirm-demo.ts
 */
import "dotenv/config";
import { writeExecutionMode, writeRiskConfig } from "../src/config/store";
import { decideExecution } from "../src/execution/executionPolicy";
import { prisma } from "../src/lib/prisma";
import { applyApprovalDecision, formatAlert } from "../src/telegram/approvals";

const SYMBOL = "DEMOUSD";
const SIG_ID = "demosig000000001";

function hr(label: string) {
  console.log(`\n${"─".repeat(64)}\n${label}\n${"─".repeat(64)}`);
}

async function cleanup() {
  await prisma.trade.deleteMany({ where: { signalId: SIG_ID } });
  await prisma.approval.deleteMany({ where: { signalId: SIG_ID } });
  await prisma.signal.deleteMany({ where: { id: SIG_ID } });
  await prisma.executionSetting.deleteMany({ where: { scope: "SYMBOL", scopeKey: SYMBOL } });
  await prisma.riskConfig.deleteMany({ where: { scope: "SYMBOL", scopeKey: SYMBOL } });
}

async function main() {
  await cleanup(); // fresh slate

  // CONFIRM for DEMOUSD; generous caps so portfolio limits never block the demo.
  await writeExecutionMode("system:demo", "SYMBOL", SYMBOL, "CONFIRM");
  await writeRiskConfig("system:demo", "SYMBOL", SYMBOL, {
    maxOpenTrades: 100,
    maxOpenRiskPct: 100,
    maxRiskPerCurrencyPct: 100,
  });

  // A signal as the gate would have persisted it (AI + risk already passed).
  const signal = await prisma.signal.create({
    data: {
      id: SIG_ID,
      symbol: SYMBOL,
      timeframe: "60min",
      direction: "LONG",
      entryPrice: "2350.00000000",
      stopLoss: "2335.00000000",
      takeProfit: "2380.00000000",
      confidenceScore: 78,
      aiReasoning:
        "Price holding above the EMA20/50 stack with RSI pulled back to 47 — trend-continuation entry. " +
        "Stop sits below the prior swing and >1×ATR, unlikely to be wicked. No HIGH-impact news in the next 5h.",
      strategyName: "trend_ema",
      status: "PENDING",
    },
  });

  hr("1 · DECIDER (mode = CONFIRM)");
  const decision = await decideExecution(signal);
  console.log(`action=${decision.action} mode=${decision.mode} reason=${decision.reason ?? "-"}`);
  const approval = await prisma.approval.findUnique({ where: { signalId: SIG_ID } });
  console.log(`approval=${approval?.status} signal=${(await sigStatus())} (stays PENDING — no trade opened)`);

  hr("2 · THE ALERT CARD YOUR PHONE WILL SHOW");
  console.log(await formatAlert(signal, 15));
  console.log("\n   [ ✅ Approve ]   [ ❌ Reject ]");

  hr("3 · APPROVE (same path as the Telegram webhook)");
  if (approval) {
    const res = await applyApprovalDecision(approval.id, true, "telegram:demo-user");
    console.log(`outcome=${res.outcome} — ${res.message}`);
    const trade = await prisma.trade.findFirst({ where: { signalId: SIG_ID } });
    console.log(
      `signal=${await sigStatus()} approval=${(await prisma.approval.findUnique({ where: { signalId: SIG_ID } }))?.status} ` +
        `trade=${trade?.status} size=${trade?.positionSize?.toString()} risk=$${trade?.riskAmount?.toString()}`,
    );
  }

  hr("CLEANUP");
  await cleanup();
  console.log("demo rows removed ✓");
}

async function sigStatus() {
  return (await prisma.signal.findUnique({ where: { id: SIG_ID }, select: { status: true } }))?.status;
}

main()
  .catch((e) => {
    console.error(e);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
    process.exit(process.exitCode ?? 0);
  });
