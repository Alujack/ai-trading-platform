/**
 * Agent-proposal layer for the weekly journal review (plan 10, Phase 3).
 *
 * The review LLM may propose config changes, but it never applies them: every
 * proposal is journaled as an AgentRecommendation row and delivered to Telegram
 * for one-tap human approval. Only an approved proposal is written to config —
 * through the same bounded, audited store functions the UI and Telegram
 * commands use. The agent's whitelist below is deliberately NARROWER than the
 * human RISK_BOUNDS: the agent can nudge, only the human can floor it.
 */

import type { ExecutionMode, Prisma } from "@prisma/client";
import { resolveExecutionMode, resolveRiskConfig } from "../config/resolve";
import type { EffectiveRiskConfig, Scope } from "../config/defaults";
import { writeExecutionMode, writeRiskConfig } from "../config/store";
import { prisma } from "../lib/prisma";
import { defaultChatId, editMessageText, isConfigured, sendMessage } from "../telegram/telegram";

const RECOMMENDATION_TTL_H = 72;
const AGENT_NAME = "weekly_review";

/** Fields the agent may propose changing, with bounds tighter than RISK_BOUNDS. */
const AGENT_RISK_BOUNDS: Partial<
  Record<keyof EffectiveRiskConfig, { min: number; max: number; int?: boolean }>
> = {
  riskPerTradePct: { min: 0.25, max: 1.5 },
  minRR: { min: 1.5, max: 4 },
  dailyLossLimitPct: { min: 0.5, max: 3 },
  dailyProfitTargetPct: { min: 1, max: 4 },
  maxOpenTrades: { min: 1, max: 3, int: true },
  maxTradesPerDay: { min: 1, max: 5, int: true },
  aiMinScore: { min: 50, max: 90, int: true },
  newsBeforeMin: { min: 15, max: 120, int: true },
  newsAfterMin: { min: 15, max: 120, int: true },
};

/** The agent may only de-escalate execution: AUTO is never proposable. */
const AGENT_ALLOWED_MODES: ExecutionMode[] = ["OFF", "CONFIRM"];

/** Strategy params the agent may shrink (de-scope), never grow. */
const AGENT_STRATEGY_FIELDS = ["symbols", "timeframes"] as const;

export interface ReviewProposal {
  entity: string;
  scope: string;
  scopeKey: string;
  field: string;
  proposedValue: string; // JSON-encoded
  rationale: string;
}

export interface TunableConfigField {
  entity: string;
  scope: string;
  scopeKey: string;
  field: string;
  currentValue: unknown;
  constraint: string;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function parseProposedValue(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw.trim(); // lenient: bare CONFIRM instead of "CONFIRM"
  }
}

function strategyParams(params: Prisma.JsonValue): Record<string, unknown> {
  return params && typeof params === "object" && !Array.isArray(params)
    ? (params as Record<string, unknown>)
    : {};
}

function stringArray(v: unknown): string[] | null {
  if (!Array.isArray(v)) return null;
  const out = v.filter((x): x is string => typeof x === "string" && x.trim() !== "");
  return out.length === v.length ? out : null;
}

/**
 * Build the tunables the review model is allowed to reason about: for every
 * enabled strategy, its effective risk fields, execution mode, and scope
 * arrays. This is both the AI-request context and the validation universe.
 */
export async function collectTunables(): Promise<TunableConfigField[]> {
  const strategies = await prisma.strategy.findMany({ where: { enabled: true } });
  const out: TunableConfigField[] = [];

  for (const s of strategies) {
    const params = strategyParams(s.params);
    const symbols = stringArray(params.symbols) ?? [];
    const primarySymbol = symbols[0] ?? "XAUUSD";

    const eff = await resolveRiskConfig(s.name, primarySymbol);
    for (const [field, b] of Object.entries(AGENT_RISK_BOUNDS)) {
      out.push({
        entity: "RiskConfig",
        scope: "STRATEGY",
        scopeKey: s.name,
        field,
        currentValue: eff[field as keyof EffectiveRiskConfig],
        constraint: `${b.min}–${b.max}${b.int ? " (integer)" : ""}`,
      });
    }

    const mode = await resolveExecutionMode(s.name, primarySymbol);
    out.push({
      entity: "ExecutionSetting",
      scope: "STRATEGY",
      scopeKey: s.name,
      field: "mode",
      currentValue: mode,
      constraint: `one of ${AGENT_ALLOWED_MODES.join("|")} — de-escalation only, AUTO is not proposable`,
    });

    for (const field of AGENT_STRATEGY_FIELDS) {
      const current = stringArray(params[field]);
      if (current && current.length > 1) {
        out.push({
          entity: "Strategy",
          scope: "STRATEGY",
          scopeKey: s.name,
          field,
          currentValue: current,
          constraint: "non-empty strict subset of current (de-scope only)",
        });
      }
    }
  }
  return out;
}

interface Validated {
  entity: "RiskConfig" | "ExecutionSetting" | "Strategy";
  scope: Scope;
  scopeKey: string;
  field: string;
  currentValue: unknown;
  value: unknown;
}

/**
 * Validate one proposal against the agent whitelist and the CURRENT config
 * state. Returns the parsed change or a rejection reason. Used both when the
 * proposal arrives and again at approve-time, so a stale approval can't apply
 * against a config that has moved underneath it.
 */
async function validateProposal(p: ReviewProposal): Promise<{ ok: true; v: Validated } | { ok: false; reason: string }> {
  const scope = p.scope as Scope;
  if (scope !== "STRATEGY" && scope !== "GLOBAL" && scope !== "SYMBOL") {
    return { ok: false, reason: `bad scope ${p.scope}` };
  }
  if (scope !== "GLOBAL" && !p.scopeKey) return { ok: false, reason: "scopeKey required" };
  const value = parseProposedValue(p.proposedValue);

  if (p.entity === "RiskConfig") {
    const b = AGENT_RISK_BOUNDS[p.field as keyof EffectiveRiskConfig];
    if (!b) return { ok: false, reason: `field ${p.field} not agent-tunable` };
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return { ok: false, reason: `${p.field} must be a number` };
    }
    if (value < b.min || value > b.max) {
      return { ok: false, reason: `${p.field}=${value} outside agent bounds ${b.min}–${b.max}` };
    }
    if (b.int && !Number.isInteger(value)) return { ok: false, reason: `${p.field} must be an integer` };
    const eff = await resolveRiskConfig(
      scope === "STRATEGY" ? p.scopeKey : undefined,
      scope === "SYMBOL" ? p.scopeKey : undefined,
    );
    const current = eff[p.field as keyof EffectiveRiskConfig];
    if (current === value) return { ok: false, reason: `${p.field} already ${value}` };
    return { ok: true, v: { entity: "RiskConfig", scope, scopeKey: p.scopeKey, field: p.field, currentValue: current, value } };
  }

  if (p.entity === "ExecutionSetting") {
    if (p.field !== "mode") return { ok: false, reason: "only mode is tunable on ExecutionSetting" };
    const mode = String(value).toUpperCase() as ExecutionMode;
    if (!AGENT_ALLOWED_MODES.includes(mode)) {
      return { ok: false, reason: `mode ${String(value)} not agent-proposable (only ${AGENT_ALLOWED_MODES.join("|")})` };
    }
    const current = await resolveExecutionMode(
      scope === "STRATEGY" ? p.scopeKey : undefined,
      scope === "SYMBOL" ? p.scopeKey : undefined,
    );
    if (current === mode) return { ok: false, reason: `mode already ${mode}` };
    return { ok: true, v: { entity: "ExecutionSetting", scope, scopeKey: p.scopeKey, field: "mode", currentValue: current, value: mode } };
  }

  if (p.entity === "Strategy") {
    if (!(AGENT_STRATEGY_FIELDS as readonly string[]).includes(p.field)) {
      return { ok: false, reason: `field ${p.field} not agent-tunable on Strategy` };
    }
    if (scope !== "STRATEGY") return { ok: false, reason: "Strategy changes must use STRATEGY scope" };
    const row = await prisma.strategy.findUnique({ where: { name: p.scopeKey } });
    if (!row) return { ok: false, reason: `unknown strategy ${p.scopeKey}` };
    const current = stringArray(strategyParams(row.params)[p.field]);
    if (!current || current.length < 2) return { ok: false, reason: `${p.field} has no room to de-scope` };
    const proposed = stringArray(value);
    if (!proposed || proposed.length === 0) return { ok: false, reason: `${p.field} must be a non-empty string array` };
    const isStrictSubset =
      proposed.length < current.length && proposed.every((x) => current.includes(x));
    if (!isStrictSubset) {
      return { ok: false, reason: `${p.field} must be a strict subset of [${current.join(",")}] — de-scope only` };
    }
    return { ok: true, v: { entity: "Strategy", scope, scopeKey: p.scopeKey, field: p.field, currentValue: current, value: proposed } };
  }

  return { ok: false, reason: `unknown entity ${p.entity}` };
}

function fmtValue(v: unknown): string {
  return Array.isArray(v) ? `[${v.join(", ")}]` : String(v);
}

function proposalAlert(rec: { id: string; entity: string; scope: string; scopeKey: string; field: string; currentValue: unknown; proposedValue: unknown; rationale: string }): string {
  return [
    `🤖 <b>AGENT PROPOSAL</b> — weekly review`,
    `${esc(rec.entity)} · ${esc(rec.scope)}:${esc(rec.scopeKey || "(global)")}`,
    ``,
    `<b>${esc(rec.field)}</b>: ${esc(fmtValue(rec.currentValue))} → <b>${esc(fmtValue(rec.proposedValue))}</b>`,
    ``,
    `<b>WHY</b>`,
    esc(rec.rationale.slice(0, 500)),
    ``,
    `Approving applies the change through the audited config path. Expires in ${RECOMMENDATION_TTL_H}h.`,
  ].join("\n");
}

export interface ProcessProposalsResult {
  created: number;
  rejected: number;
  alerted: number;
}

/**
 * Journal every valid proposal as a PENDING AgentRecommendation and push the
 * Telegram approval card. Invalid proposals are logged and dropped — the agent
 * gets no second opinion. Fail-safe like signal approvals: no Telegram means
 * the row is still journaled (visible in DB/dashboard), it just can't be
 * approved until the operator configures the bot.
 */
export async function processReviewProposals(proposals: ReviewProposal[]): Promise<ProcessProposalsResult> {
  let created = 0;
  let rejected = 0;
  let alerted = 0;

  for (const p of proposals.slice(0, 5)) {
    const res = await validateProposal(p);
    if (!res.ok) {
      rejected += 1;
      console.warn(
        `[reviewAgent] proposal_rejected entity=${p.entity} scope=${p.scope}:${p.scopeKey} field=${p.field} reason="${res.reason}"`,
      );
      continue;
    }
    const v = res.v;

    const dupe = await prisma.agentRecommendation.findFirst({
      where: { status: "PENDING", entity: v.entity, scope: v.scope, scopeKey: v.scopeKey, field: v.field },
    });
    if (dupe) {
      rejected += 1;
      console.warn(`[reviewAgent] proposal_rejected field=${v.field} reason="pending_duplicate ${dupe.id}"`);
      continue;
    }

    const rec = await prisma.agentRecommendation.create({
      data: {
        agent: AGENT_NAME,
        entity: v.entity,
        scope: v.scope,
        scopeKey: v.scopeKey,
        field: v.field,
        currentValue: v.currentValue as Prisma.InputJsonValue,
        proposedValue: v.value as Prisma.InputJsonValue,
        rationale: p.rationale,
        chatId: defaultChatId(),
        expiresAt: new Date(Date.now() + RECOMMENDATION_TTL_H * 3_600_000),
      },
    });
    created += 1;
    console.log(
      `[reviewAgent] recommendation_created id=${rec.id} ${v.entity} ${v.scope}:${v.scopeKey} ${v.field}=${fmtValue(v.value)}`,
    );

    if (!isConfigured() || !rec.chatId) {
      console.warn(`[reviewAgent] telegram_not_configured — recommendation ${rec.id} journaled without alert`);
      continue;
    }
    const messageId = await sendMessage(rec.chatId, proposalAlert(rec), [
      [
        { text: "✅ Approve", callback_data: `rca:${rec.id}` },
        { text: "❌ Reject", callback_data: `rcr:${rec.id}` },
      ],
    ]);
    if (messageId) {
      await prisma.agentRecommendation.update({ where: { id: rec.id }, data: { messageId } });
      alerted += 1;
    }
  }
  return { created, rejected, alerted };
}

export interface RecommendationDecision {
  ok: boolean;
  outcome: "approved" | "rejected" | "already_decided" | "expired" | "not_found" | "apply_failed";
  message: string;
}

/**
 * Apply an Approve/Reject decision for an agent recommendation. Idempotent.
 * Approval re-validates against the live config, then writes through the
 * audited store path with an actor string that names both the human and the
 * agent (e.g. "telegram:123 via weekly_review").
 */
export async function applyRecommendationDecision(
  recommendationId: string,
  approve: boolean,
  decidedBy: string,
): Promise<RecommendationDecision> {
  const rec = await prisma.agentRecommendation.findUnique({ where: { id: recommendationId } });
  if (!rec) return { ok: false, outcome: "not_found", message: "Recommendation not found." };
  if (rec.status !== "PENDING") {
    return { ok: false, outcome: "already_decided", message: `Already ${rec.status.toLowerCase()}.` };
  }
  if (rec.expiresAt.getTime() < Date.now()) {
    await prisma.agentRecommendation.update({ where: { id: rec.id }, data: { status: "EXPIRED" } });
    return { ok: false, outcome: "expired", message: "This recommendation already expired." };
  }

  const stamp = new Date();
  if (!approve) {
    await prisma.agentRecommendation.update({
      where: { id: rec.id },
      data: { status: "REJECTED", decidedBy, decidedAt: stamp },
    });
    return { ok: true, outcome: "rejected", message: `❌ Rejected by ${decidedBy}` };
  }

  // Re-validate against the config as it is NOW, not as it was at proposal time.
  const revalidated = await validateProposal({
    entity: rec.entity,
    scope: rec.scope,
    scopeKey: rec.scopeKey,
    field: rec.field,
    proposedValue: JSON.stringify(rec.proposedValue),
    rationale: rec.rationale,
  });
  if (!revalidated.ok) {
    return { ok: false, outcome: "apply_failed", message: `Could not apply: ${revalidated.reason}` };
  }
  const v = revalidated.v;
  const actor = `${decidedBy} via ${AGENT_NAME}`;

  let applied: { ok: true } | { ok: false; error: string };
  if (v.entity === "RiskConfig") {
    applied = await writeRiskConfig(actor, v.scope, v.scopeKey, { [v.field]: v.value });
  } else if (v.entity === "ExecutionSetting") {
    applied = await writeExecutionMode(actor, v.scope, v.scopeKey, v.value as ExecutionMode);
  } else {
    applied = await applyStrategyDescope(actor, v.scopeKey, v.field, v.value as string[]);
  }
  if (!applied.ok) {
    return { ok: false, outcome: "apply_failed", message: `Could not apply: ${applied.error}` };
  }

  await prisma.agentRecommendation.update({
    where: { id: rec.id },
    data: { status: "APPROVED", decidedBy, decidedAt: stamp, appliedAt: stamp },
  });
  return { ok: true, outcome: "approved", message: `✅ Approved by ${decidedBy} · change applied` };
}

/** De-scope a strategy's symbols/timeframes params, with a ConfigAudit entry. */
async function applyStrategyDescope(
  actor: string,
  strategyName: string,
  field: string,
  proposed: string[],
): Promise<{ ok: true } | { ok: false; error: string }> {
  const row = await prisma.strategy.findUnique({ where: { name: strategyName } });
  if (!row) return { ok: false, error: `unknown strategy ${strategyName}` };
  const before = strategyParams(row.params);
  const after = { ...before, [field]: proposed };
  await prisma.strategy.update({ where: { name: strategyName }, data: { params: after as Prisma.InputJsonValue } });
  try {
    await prisma.configAudit.create({
      data: {
        actor,
        entity: "Strategy",
        scope: "STRATEGY",
        scopeKey: strategyName,
        before: before as Prisma.InputJsonValue,
        after: after as Prisma.InputJsonValue,
      },
    });
  } catch (err) {
    console.error("[reviewAgent] audit write failed:", err instanceof Error ? err.message : err);
  }
  return { ok: true };
}

/**
 * Expire PENDING recommendations past their TTL and stamp the Telegram card.
 * Runs from the same minute cron as signal-approval expiry.
 */
export async function expireStaleRecommendations(): Promise<{ expired: number }> {
  const stale = await prisma.agentRecommendation.findMany({
    where: { status: "PENDING", expiresAt: { lt: new Date() } },
    take: 100,
  });
  let expired = 0;
  for (const r of stale) {
    await prisma.agentRecommendation.update({ where: { id: r.id }, data: { status: "EXPIRED" } });
    if (r.chatId && r.messageId) {
      await editMessageText(r.chatId, r.messageId, "⌛ <b>Expired</b> — recommendation not acted on.");
    }
    expired += 1;
  }
  return { expired };
}
