import { Router } from "express";
import { redis } from "../lib/redis";
import { publishEvent, RT_CHANNEL, type RtEvent } from "../lib/realtime";
import { asyncHandler } from "../middleware/asyncHandler";

const router = Router();

// Server-Sent Events: pushes one message per realtime event so the dashboard
// updates the instant new candles/signals land — no polling lag.
router.get("/stream", async (req, res) => {
  res.set({
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.flushHeaders?.();
  res.write(`event: hello\ndata: {"ok":true}\n\n`);

  // Each client gets its own subscriber connection (a subscribed ioredis
  // connection can't issue other commands).
  const sub = redis.duplicate();
  try {
    await sub.connect();
    await sub.subscribe(RT_CHANNEL);
  } catch (err) {
    console.error("[rt] subscribe failed:", err instanceof Error ? err.message : err);
    res.write(`event: error\ndata: {"ok":false}\n\n`);
  }

  sub.on("message", (_channel, message) => {
    res.write(`data: ${message}\n\n`);
  });

  const heartbeat = setInterval(() => res.write(": ping\n\n"), 25_000);

  req.on("close", () => {
    clearInterval(heartbeat);
    sub.removeAllListeners("message");
    sub.quit().catch(() => undefined);
    res.end();
  });
});

// The Python worker can't reach Redis directly, so it pings this after writing
// candles/indicators; we fan it out to SSE clients.
router.post(
  "/internal/rt-notify",
  asyncHandler(async (req, res) => {
    const { type, symbol, timeframe } = (req.body ?? {}) as RtEvent;
    if (type) await publishEvent({ type, symbol, timeframe });
    res.status(202).json({ ok: true });
  }),
);

export default router;
