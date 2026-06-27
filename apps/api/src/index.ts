// Must be first: loads the root .env before any module reads process.env.
import "./lib/load-env";
import { buildApp } from "./app";
import { prisma } from "./lib/prisma";
import { redis, connectRedis } from "./lib/redis";
import {
  startPaperTradingScheduler,
  startWeeklyReviewScheduler,
  startDailyBriefingScheduler,
  runDailyBriefingOnce,
  stopExecutionSchedulers,
} from "./execution/scheduler";

const port = Number(process.env.API_PORT ?? 4000);
const host = process.env.API_HOST ?? "0.0.0.0";

const app = buildApp();

connectRedis().catch((err) => {
  console.error("[redis] initial connect failed:", err.message);
});

const server = app.listen(port, host, () => {
  console.log(`API listening on http://${host}:${port}`);
});

// Signal generation moved to the Python strategy runner (services/data);
// strategies now POST candidates to POST /api/signals/candidate.

// When live (BROKER=exness), push the UI-configured MT5 credentials to the
// bridge so the terminal is logged into the right account on boot. Best-effort:
// a missing/failed login is logged, not fatal — the user can fix it in Settings.
void import("./execution/broker").then(({ ensureBrokerSession }) =>
  ensureBrokerSession()
    .then((r) => console.log(`[broker] startup session: ok=${r.ok} ${r.detail}`))
    .catch((err) => console.error("[broker] startup session failed:", err instanceof Error ? err.message : err)),
);

if (process.env.ENABLE_PAPER_TRADING === "true") {
  startPaperTradingScheduler();
}

if (process.env.ENABLE_WEEKLY_REVIEW === "true") {
  startWeeklyReviewScheduler();
}

// Daily briefing: the agent's morning routine. Runs once on startup (a summary
// every time the system starts) and then daily at 06:00 UTC.
if (process.env.ENABLE_DAILY_BRIEFING !== "false") {
  startDailyBriefingScheduler();
  void runDailyBriefingOnce();
}

async function shutdown(signal: string): Promise<void> {
  console.log(`[shutdown] received ${signal}, closing gracefully`);
  stopExecutionSchedulers();
  server.close(() => console.log("[shutdown] http server closed"));
  try {
    await prisma.$disconnect();
  } catch (err) {
    console.error("[shutdown] prisma disconnect failed:", err);
  }
  try {
    await redis.quit();
  } catch (err) {
    console.error("[shutdown] redis quit failed:", err);
  }
  process.exit(0);
}

process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));
