import { Router } from "express";
import { prisma } from "../lib/prisma";
import { pingRedis } from "../lib/redis";
import { asyncHandler } from "../middleware/asyncHandler";

const router = Router();

async function pingDb(timeoutMs = 1500): Promise<boolean> {
  try {
    await Promise.race([
      prisma.$queryRaw`SELECT 1`,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("db ping timeout")), timeoutMs),
      ),
    ]);
    return true;
  } catch {
    return false;
  }
}

router.get(
  "/health",
  asyncHandler(async (_req, res) => {
    const [dbOk, redisOk] = await Promise.all([pingDb(), pingRedis()]);
    const status = dbOk && redisOk ? "ok" : "degraded";
    const body = {
      status,
      db: dbOk ? "connected" : "disconnected",
      redis: redisOk ? "connected" : "disconnected",
    };
    res.status(dbOk && redisOk ? 200 : 503).json(body);
  }),
);

export default router;
