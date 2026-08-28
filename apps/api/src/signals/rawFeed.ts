import type { RawVerdict } from "@prisma/client";
import { RAW_FEED_FLAG, isFlagEnabled } from "../config/flags";
import { prisma } from "../lib/prisma";
import type { GateResult, GateStatus, SignalCandidate } from "./gate";

/**
 * The raw ("layers off") strategy feed.
 *
 * With the `raw_signal_feed` flag on, every candidate is written here the moment
 * it reaches the gate — before the AI validator, the risk engine or the regime
 * gate get a say — and stamped afterwards with whichever layer stopped it. That
 * gives the operator the untouched strategy view for manual trading while
 * automation keeps running the full stack unchanged.
 *
 * This module is observe-only by construction: it writes RawSignal rows and
 * nothing else, RawSignal has no relation into Trade/Approval, and no module
 * under execution/ imports it. Every function is best-effort — a raw-feed
 * failure must never break, delay or alter the real gate verdict.
 */

/** Layer tag recorded on a blocked raw candidate (see RawSignal.blockedBy). */
export type BlockedBy =
  | "duplicate"
  | "cooldown"
  | "insufficient_candles"
  | "ai_unreachable"
  | "ai_score"
  | "ai_judgment"
  | "regime"
  | "risk_inputs"
  | "risk_daily_loss"
  | "risk_drawdown"
  | "risk_rr"
  | "risk_news"
  | "risk_gold"
  | "risk"
  | "unknown";

export interface GateOutcomeClass {
  verdict: RawVerdict;
  blockedBy: BlockedBy | null;
}

/**
 * Which layer stopped this candidate? Pure, so it is unit-tested against the
 * exact reason strings gate.ts and riskEngine.ts produce.
 *
 * The risk engine collects ALL its failures into one joined reason string, so
 * sub-classification follows validateTrade()'s own push order (inputs → daily
 * loss → drawdown → RR → news → gold) and reports the first one it recorded.
 */
export function classifyGateOutcome(status: GateStatus, reason?: string): GateOutcomeClass {
  if (status === "generated") return { verdict: "GENERATED", blockedBy: null };
  const verdict: RawVerdict = status === "rejected" ? "REJECTED" : "SKIPPED";
  const r = reason ?? "";

  const tag = ((): BlockedBy => {
    if (r.startsWith("idempotent_duplicate")) return "duplicate";
    if (r.startsWith("cooldown_active")) return "cooldown";
    if (r.startsWith("insufficient_candles")) return "insufficient_candles";
    if (r.startsWith("ai_service_") || r.startsWith("ai_service_unreachable")) return "ai_unreachable";
    if (r.startsWith("ai_score_too_low")) return "ai_score";
    if (r.startsWith("ai_not_approved")) return "ai_judgment";
    if (r.startsWith("pre_gated_regime")) return "regime";
    if (r.startsWith("risk_rejected")) {
      if (/must be|must differ/i.test(r)) return "risk_inputs";
      if (/daily loss limit/i.test(r)) return "risk_daily_loss";
      if (/drawdown/i.test(r)) return "risk_drawdown";
      if (/risk\/reward/i.test(r)) return "risk_rr";
      if (/news window/i.test(r)) return "risk_news";
      if (/^risk_rejected:\s*gold|;\s*gold/i.test(r)) return "risk_gold";
      return "risk";
    }
    return "unknown";
  })();

  return { verdict, blockedBy: tag };
}

/**
 * Key that collapses re-emissions of the same proposal. A strategy carrying a
 * clientId (a per-bar hash) dedupes on that; otherwise we key on the levels plus
 * the UTC date, so a 1-minute scan loop re-proposing an identical setup bumps
 * one row instead of flooding the feed.
 */
export function dedupeKeyFor(c: SignalCandidate, now: Date = new Date()): string {
  if (c.clientId) return `cid:${c.clientId}`;
  const day = now.toISOString().slice(0, 10);
  const levels = [c.entryPrice, c.stopLoss, c.takeProfit]
    .map((n) => (Number.isFinite(n) ? n.toFixed(8) : "nan"))
    .join("/");
  return `auto:${day}:${c.strategyName}:${c.symbol}:${c.timeframe}:${c.direction}:${levels}`;
}

/** Is the raw feed on right now? */
export function rawFeedEnabled(): Promise<boolean> {
  return isFlagEnabled(RAW_FEED_FLAG);
}

/**
 * Record the untouched candidate and return its RawSignal id, or null when the
 * feed is off (or the write failed — the gate carries on regardless).
 */
export async function recordRawCandidate(candidate: SignalCandidate): Promise<string | null> {
  if (!(await rawFeedEnabled())) return null;
  const dedupeKey = dedupeKeyFor(candidate);
  const base = {
    symbol: candidate.symbol,
    timeframe: candidate.timeframe,
    direction: candidate.direction,
    entryPrice: candidate.entryPrice.toFixed(8),
    stopLoss: candidate.stopLoss.toFixed(8),
    takeProfit: candidate.takeProfit.toFixed(8),
    confidence: Math.round(candidate.confidence),
    reasoning: candidate.reasoning,
    strategyName: candidate.strategyName,
  };
  try {
    const row = await prisma.rawSignal.upsert({
      where: { dedupeKey },
      create: { ...base, dedupeKey },
      // A re-emission counts as another sighting and refreshes the levels. The
      // verdict is deliberately NOT reset here: stampRawVerdict overwrites it a
      // moment later, and leaving the previous one in place means the row never
      // flickers back to PENDING mid-scan.
      update: { ...base, seenCount: { increment: 1 }, lastSeenAt: new Date() },
      select: { id: true },
    });
    return row.id;
  } catch (err) {
    console.error("[rawfeed] record failed:", err instanceof Error ? err.message : err);
    return null;
  }
}

/**
 * Layers that only fire BECAUSE an identical candidate already went through. A
 * row that once cleared everything must not be downgraded to one of these by a
 * later re-emission — otherwise the feed reports "duplicate" for a setup the desk
 * actually took.
 */
const NON_DOWNGRADING: ReadonlySet<string> = new Set(["duplicate", "cooldown"]);

/** Stamp the layer verdict onto a previously recorded raw candidate. */
export async function stampRawVerdict(rawId: string | null, result: GateResult): Promise<void> {
  if (!rawId) return;
  const { verdict, blockedBy } = classifyGateOutcome(result.status, result.reason);
  const data = {
    verdict,
    blockedBy,
    blockedReason: result.status === "generated" ? null : (result.reason ?? null),
    signalId: result.signalId ?? null,
  };
  try {
    if (blockedBy && NON_DOWNGRADING.has(blockedBy)) {
      // updateMany so the verdict guard lives in the WHERE clause: a GENERATED
      // row keeps its verdict and signalId, everything else is stamped.
      await prisma.rawSignal.updateMany({
        where: { id: rawId, verdict: { not: "GENERATED" } },
        data,
      });
      return;
    }
    await prisma.rawSignal.update({ where: { id: rawId }, data });
  } catch (err) {
    console.error("[rawfeed] stamp failed:", err instanceof Error ? err.message : err);
  }
}
