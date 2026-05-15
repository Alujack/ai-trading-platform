import Redis from "ioredis";

const globalForRedis = globalThis as unknown as { redis?: Redis };

export const redis =
  globalForRedis.redis ??
  new Redis(process.env.REDIS_URL ?? "redis://localhost:6379", {
    lazyConnect: true,
    maxRetriesPerRequest: 2,
    enableOfflineQueue: false,
  });

if (process.env.NODE_ENV !== "production") {
  globalForRedis.redis = redis;
}

redis.on("error", (err) => {
  console.error("[redis] error:", err.message);
});

let connectPromise: Promise<void> | null = null;

export function connectRedis(): Promise<void> {
  if (redis.status === "ready" || redis.status === "connecting") {
    return Promise.resolve();
  }
  if (!connectPromise) {
    connectPromise = redis.connect().catch((err) => {
      connectPromise = null;
      throw err;
    });
  }
  return connectPromise;
}

export async function pingRedis(timeoutMs = 1500): Promise<boolean> {
  try {
    await connectRedis();
    const result = await Promise.race<string>([
      redis.ping(),
      new Promise<string>((_, reject) =>
        setTimeout(() => reject(new Error("redis ping timeout")), timeoutMs),
      ),
    ]);
    return result === "PONG";
  } catch {
    return false;
  }
}
