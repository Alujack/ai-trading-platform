/** One-off: reconcile open live trades against the broker (close + journal). */
import "dotenv/config";
import { monitorLiveTrades } from "../src/execution/liveTrade";
import { prisma } from "../src/lib/prisma";

monitorLiveTrades()
  .then((s) => console.log("monitor:", JSON.stringify(s)))
  .catch((e) => { console.error(e); process.exitCode = 1; })
  .finally(async () => { await prisma.$disconnect(); process.exit(process.exitCode ?? 0); });
