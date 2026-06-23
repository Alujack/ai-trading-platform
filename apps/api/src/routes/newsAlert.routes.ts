import { Router } from "express";
import { redis } from "../lib/redis";
import { asyncHandler } from "../middleware/asyncHandler";

const router = Router();

// How long a HIGH-impact alert stays "active" in the cache. The risk engine
// already enforces the ±30-min blackout off the NewsEvent rows; this key is a
// fast, proactive signal the dashboard/API can read without a DB scan.
const ALERT_TTL_SECONDS = 90 * 60;
const ALERT_KEY = "news:high-impact:active";

interface NewsAlertBody {
  title?: unknown;
  currency?: unknown;
  scheduledAt?: unknown;
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

/**
 * Receives HIGH-impact calendar alerts from n8n (Workflow A's optional branch)
 * so the system reacts immediately instead of waiting for the next signal
 * cycle. Idempotent: writes/refreshes a single Redis key the UI can poll.
 */
router.post(
  "/internal/news-alert",
  asyncHandler(async (req, res) => {
    const body = (req.body ?? {}) as NewsAlertBody;
    const title = asString(body.title);
    const currency = asString(body.currency);
    const scheduledAt = asString(body.scheduledAt);

    if (!title || !scheduledAt) {
      res.status(400).json({ error: "title and scheduledAt are required" });
      return;
    }

    const payload = JSON.stringify({
      title,
      currency: currency ?? "UNKNOWN",
      scheduledAt,
      receivedAt: new Date().toISOString(),
    });

    try {
      await redis.set(ALERT_KEY, payload, "EX", ALERT_TTL_SECONDS);
    } catch (err) {
      console.error("[news-alert] failed to cache alert:", err);
    }
    console.log(`[news-alert] HIGH impact ${currency ?? "?"} "${title}" @ ${scheduledAt}`);

    res.status(202).json({ ok: true });
  }),
);

/** Lets the dashboard surface a banner without hitting Postgres. */
router.get(
  "/internal/news-alert",
  asyncHandler(async (_req, res) => {
    let active: unknown = null;
    try {
      const raw = await redis.get(ALERT_KEY);
      active = raw ? JSON.parse(raw) : null;
    } catch (err) {
      console.error("[news-alert] failed to read alert:", err);
    }
    res.json({ active });
  }),
);

export default router;
