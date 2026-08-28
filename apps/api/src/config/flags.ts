import type { Prisma } from "@prisma/client";
import { prisma } from "../lib/prisma";
import { connectRedis, redis } from "../lib/redis";

/**
 * Runtime feature flags — the same pattern as config/resolve.ts (Redis-cached
 * DB rows, busted on write, audited in ConfigAudit), for booleans the dashboard
 * flips rather than numeric risk fields.
 *
 * Resolution: DB row ► env default ► false. So a flag is off until someone
 * either sets the env var or flips it in the UI, and the UI always wins.
 */

/** Record every raw strategy candidate (protection layers OFF, observe-only). */
export const RAW_FEED_FLAG = "raw_signal_feed";

/** Env var consulted when a flag has no DB row yet. */
const ENV_DEFAULTS: Record<string, string> = {
  [RAW_FEED_FLAG]: "RAW_SIGNAL_FEED",
};

const FLAG_CACHE_KEY = "config:flags:rows";
const CACHE_TTL_S = 300; // safety net; writes bust the cache explicitly

export type FlagSource = "db" | "env" | "default";

export interface FlagState {
  key: string;
  enabled: boolean;
  source: FlagSource;
}

type FlagRow = { key: string; enabled: boolean };

function envDefault(key: string): boolean | null {
  const envName = ENV_DEFAULTS[key];
  if (!envName) return null;
  const raw = process.env[envName];
  if (raw == null || raw.trim() === "") return null;
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

async function getFlagRows(): Promise<FlagRow[]> {
  try {
    await connectRedis();
    const cached = await redis.get(FLAG_CACHE_KEY);
    if (cached) return JSON.parse(cached) as FlagRow[];
  } catch {
    /* fall through to DB */
  }
  const rows = await prisma.featureFlag.findMany({ select: { key: true, enabled: true } });
  try {
    await redis.set(FLAG_CACHE_KEY, JSON.stringify(rows), "EX", CACHE_TTL_S);
  } catch {
    /* cache is best-effort */
  }
  return rows;
}

/** Invalidate the flag cache — call after any flag write. */
export async function bustFlagCache(): Promise<void> {
  try {
    await connectRedis();
    await redis.del(FLAG_CACHE_KEY);
  } catch {
    /* best-effort */
  }
}

export async function getFlag(key: string): Promise<FlagState> {
  const row = (await getFlagRows()).find((r) => r.key === key);
  if (row) return { key, enabled: row.enabled, source: "db" };
  const env = envDefault(key);
  if (env != null) return { key, enabled: env, source: "env" };
  return { key, enabled: false, source: "default" };
}

/**
 * Is a flag on? Never throws: an unreachable DB/Redis resolves to the env
 * default (then false), so a flag outage can only turn a feature OFF.
 */
export async function isFlagEnabled(key: string): Promise<boolean> {
  try {
    return (await getFlag(key)).enabled;
  } catch (err) {
    console.error("[flags] resolve failed:", err instanceof Error ? err.message : err);
    return envDefault(key) ?? false;
  }
}

/** Convenience for the hot path in the signal gate. */
export function isRawFeedEnabled(): Promise<boolean> {
  return isFlagEnabled(RAW_FEED_FLAG);
}

export async function setFlag(actor: string, key: string, enabled: boolean): Promise<FlagState> {
  const before = await prisma.featureFlag.findUnique({ where: { key } });
  const after = await prisma.featureFlag.upsert({
    where: { key },
    create: { key, enabled },
    update: { enabled },
  });

  try {
    await prisma.configAudit.create({
      data: {
        actor,
        entity: "FeatureFlag",
        scope: "GLOBAL",
        scopeKey: key,
        before: (before ?? {}) as unknown as Prisma.InputJsonValue,
        after: after as unknown as Prisma.InputJsonValue,
      },
    });
  } catch (err) {
    console.error("[flags] audit write failed:", err instanceof Error ? err.message : err);
  }

  await bustFlagCache();
  return { key, enabled: after.enabled, source: "db" };
}
