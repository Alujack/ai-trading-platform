import type { ExecutionMode, Prisma } from "@prisma/client";
import { prisma } from "../lib/prisma";
import {
  type EffectiveRiskConfig,
  RISK_BOUNDS,
  type Scope,
} from "./defaults";
import { bustConfigCache } from "./resolve";

/**
 * Write side of the config layer. Every mutation validates against the hard
 * bounds, appends a ConfigAudit row (who-changed-what), and busts the resolver
 * cache. Shared by the /api/config/* routes and the Telegram /mode /kill /arm
 * commands so both paths are audited identically.
 */

export type RiskFields = Partial<EffectiveRiskConfig> & { enabled?: boolean };

const RISK_NUMERIC: (keyof EffectiveRiskConfig)[] = Object.keys(RISK_BOUNDS) as (keyof EffectiveRiskConfig)[];

export function validateRiskFields(fields: RiskFields): string | null {
  for (const key of RISK_NUMERIC) {
    const v = fields[key];
    if (v == null) continue;
    if (typeof v !== "number" || !Number.isFinite(v)) return `${key} must be a number`;
    const b = RISK_BOUNDS[key];
    if (v < b.min || v > b.max) return `${key} must be between ${b.min} and ${b.max}`;
    if (b.int && !Number.isInteger(v)) return `${key} must be an integer`;
  }
  return null;
}

function normalizeKey(scope: Scope, scopeKey: string): string {
  return scope === "GLOBAL" ? "" : scopeKey;
}

async function audit(
  actor: string,
  entity: string,
  scope: string,
  scopeKey: string,
  before: unknown,
  after: unknown,
): Promise<void> {
  try {
    await prisma.configAudit.create({
      data: {
        actor,
        entity,
        scope,
        scopeKey,
        before: (before ?? {}) as Prisma.InputJsonValue,
        after: (after ?? {}) as Prisma.InputJsonValue,
      },
    });
  } catch (err) {
    console.error("[config] audit write failed:", err instanceof Error ? err.message : err);
  }
}

export async function writeRiskConfig(
  actor: string,
  scope: Scope,
  scopeKeyRaw: string,
  fields: RiskFields,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const err = validateRiskFields(fields);
  if (err) return { ok: false, error: err };
  const scopeKey = normalizeKey(scope, scopeKeyRaw);
  if (scope !== "GLOBAL" && !scopeKey) return { ok: false, error: "scopeKey required for non-global scope" };

  const before = await prisma.riskConfig.findUnique({ where: { scope_scopeKey: { scope, scopeKey } } });

  const data: Record<string, unknown> = {};
  for (const key of RISK_NUMERIC) {
    if (fields[key] != null) data[key] = fields[key];
  }
  if (fields.enabled != null) data.enabled = fields.enabled;

  const after = await prisma.riskConfig.upsert({
    where: { scope_scopeKey: { scope, scopeKey } },
    create: { scope, scopeKey, ...data },
    update: data,
  });

  await audit(actor, "RiskConfig", scope, scopeKey, before, after);
  await bustConfigCache();
  return { ok: true };
}

export async function writeExecutionMode(
  actor: string,
  scope: Scope,
  scopeKeyRaw: string,
  mode: ExecutionMode,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const scopeKey = normalizeKey(scope, scopeKeyRaw);
  if (scope !== "GLOBAL" && !scopeKey) return { ok: false, error: "scopeKey required for non-global scope" };

  const before = await prisma.executionSetting.findUnique({
    where: { scope_scopeKey: { scope, scopeKey } },
  });
  const after = await prisma.executionSetting.upsert({
    where: { scope_scopeKey: { scope, scopeKey } },
    create: { scope, scopeKey, mode },
    update: { mode },
  });

  await audit(actor, "ExecutionSetting", scope, scopeKey, before, after);
  await bustConfigCache();
  return { ok: true };
}

/** Panic kill-switch: GLOBAL mode = OFF. */
export async function setKillSwitch(actor: string): Promise<void> {
  await writeExecutionMode(actor, "GLOBAL", "", "OFF");
}

/** Clear a manual kill: GLOBAL mode back to CONFIRM (safe default). */
export async function armSystem(actor: string): Promise<void> {
  await writeExecutionMode(actor, "GLOBAL", "", "CONFIRM");
}
