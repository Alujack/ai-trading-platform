import "dotenv/config";
import { buildApp } from "./app";
import { prisma } from "./lib/prisma";
import { redis, connectRedis } from "./lib/redis";
import {
  startPaperTradingScheduler,
  startWeeklyReviewScheduler,
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

if (process.env.ENABLE_PAPER_TRADING === "true") {
  startPaperTradingScheduler();
}

if (process.env.ENABLE_WEEKLY_REVIEW === "true") {
  startWeeklyReviewScheduler();
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
