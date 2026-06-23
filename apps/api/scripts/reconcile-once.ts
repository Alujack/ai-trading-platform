/** One-off: run the decider over all undecided PENDING signals right now
 *  (instead of waiting for the 5-min cron). Sends Telegram alerts for any that
 *  resolve to CONFIRM. */
import "dotenv/config";
import { reconcilePendingSignals } from "../src/execution/executionPolicy";
import { prisma } from "../src/lib/prisma";

reconcilePendingSignals()
  .then((s) => console.log("reconcile:", JSON.stringify(s)))
  .catch((e) => {
    console.error(e);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
    process.exit(process.exitCode ?? 0);
  });
