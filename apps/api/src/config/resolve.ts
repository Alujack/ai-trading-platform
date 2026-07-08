import type { ExecutionMode, RiskConfig } from "@prisma/client";
import { prisma } from "../lib/prisma";
import { connectRedis, redis } from "../lib/redis";
import {
  type EffectiveRiskConfig,
  RISK_DEFAULTS,
  type Scope,
} from "./defaults";

/**
 * Config resolver. Reads the three scope rows (GLOBAL/STRATEGY/SYMBOL), layers
 * them most-specific-wins per field, caches the raw rows in Redis (busted on
 * write), and hands a single EffectiveRiskConfig / ExecutionMode to the gate,
 * the risk engine and the decider.
 */

const RISK_CACHE_KEY = "config:risk:rows";
const EXEC_CACHE_KEY = "config:exec:rows";
const CACHE_TTL_S = 300; // safety net; writes bust the cache explicitly

const RISK_FIELDS: (keyof EffectiveRiskConfig)[] = [
  "riskPerTradePct",
  "minRR",
  "dailyLossLimitPct",
  "maxDrawdownPct",
  "maxOpenTrades",
  "maxTradesPerDay",
  "maxOpenRiskPct",
  "maxRiskPerCurrencyPct",
  "newsBeforeMin",
  "newsAfterMin",
  "aiMinScore",
  "approvalTtlMin",
];

type RiskRow = Pick<RiskConfig, "scope" | "scopeKey" | "enabled"> &
  Record<keyof EffectiveRiskConfig, unknown>;
type ExecRow = { scope: string; scopeKey: string; mode: ExecutionMode };

function num(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : Number((v as { toString(): string }).toString());
  return Number.isFinite(n) ? n : null;
}

async function getRiskRows(): Promise<RiskRow[]> {
  try {
    await connectRedis();
    const cached = await redis.get(RISK_CACHE_KEY);
    if (cached) return JSON.parse(cached) as RiskRow[];
  } catch {
    /* fall through to DB */
  }
  const rows = (await prisma.riskConfig.findMany()) as unknown as RiskRow[];
  try {
    await redis.set(RISK_CACHE_KEY, JSON.stringify(rows), "EX", CACHE_TTL_S);
  } catch {
    /* cache is best-effort */
  }
  return rows;
}

async function getExecRows(): Promise<ExecRow[]> {
  try {
    await connectRedis();
    const cached = await redis.get(EXEC_CACHE_KEY);
    if (cached) return JSON.parse(cached) as ExecRow[];
  } catch {
    /* fall through to DB */
  }
  const rows = await prisma.executionSetting.findMany({
    select: { scope: true, scopeKey: true, mode: true },
  });
  try {
    await redis.set(EXEC_CACHE_KEY, JSON.stringify(rows), "EX", CACHE_TTL_S);
  } catch {
    /* cache is best-effort */
  }
  return rows;
}

/** Invalidate both caches — call after any config/mode write. */
export async function bustConfigCache(): Promise<void> {
  try {
    await connectRedis();
    await redis.del(RISK_CACHE_KEY, EXEC_CACHE_KEY);
  } catch {
    /* best-effort */
  }
}

function pickRow<T extends { scope: string; scopeKey: string }>(
  rows: T[],
  scope: Scope,
  scopeKey: string,
): T | undefined {
  return rows.find((r) => r.scope === scope && r.scopeKey === (scope === "GLOBAL" ? "" : scopeKey));
}

export async function resolveRiskConfig(
  strategyName?: string | null,
  symbol?: string | null,
): Promise<EffectiveRiskConfig> {
  const rows = await getRiskRows();
  const global = pickRow(rows, "GLOBAL", "");
  const strat = strategyName ? pickRow(rows, "STRATEGY", strategyName) : undefined;
  const sym = symbol ? pickRow(rows, "SYMBOL", symbol) : undefined;

  // A disabled override row is ignored entirely (acts as if absent).
  const layers = [sym, strat, global].filter((r) => r && r.enabled) as RiskRow[];

  const out = { ...RISK_DEFAULTS };
  for (const field of RISK_FIELDS) {
    for (const row of layers) {
      const v = num(row[field]);
      if (v != null) {
        out[field] = v;
        break; // most-specific layer that has a value wins
      }
    }
  }
  return out;
}

export async function resolveExecutionMode(
  strategyName?: string | null,
  symbol?: string | null,
): Promise<ExecutionMode> {
  const rows = await getExecRows();
  const sym = symbol ? pickRow(rows, "SYMBOL", symbol) : undefined;
  if (sym) return sym.mode;
  const strat = strategyName ? pickRow(rows, "STRATEGY", strategyName) : undefined;
  if (strat) return strat.mode;
  const global = pickRow(rows, "GLOBAL", "");
  return global?.mode ?? "CONFIRM";
}

export interface ExecModeMap {
  global: ExecutionMode;
  rows: ExecRow[];
}

export async function getExecutionMap(): Promise<ExecModeMap> {
  const rows = await getExecRows();
  const global = pickRow(rows, "GLOBAL", "");
  return { global: global?.mode ?? "CONFIRM", rows };
}
