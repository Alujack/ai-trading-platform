import { connectRedis, redis } from "./redis";

export const RT_CHANNEL = "rt";

export interface RtEvent {
  type: "candle" | "signal" | "trade" | "news";
  symbol?: string;
  timeframe?: string;
  at?: number;
}

// Fan an event out to all connected SSE clients via Redis pub/sub. Best-effort:
// a Redis hiccup must never break the caller (signal creation, candle ingest).
export async function publishEvent(event: RtEvent): Promise<void> {
  try {
    await connectRedis();
    await redis.publish(RT_CHANNEL, JSON.stringify({ ...event, at: Date.now() }));
  } catch (err) {
    console.error("[rt] publish failed:", err instanceof Error ? err.message : err);
  }
}
