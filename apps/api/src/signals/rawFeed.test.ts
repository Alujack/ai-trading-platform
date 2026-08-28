import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { classifyGateOutcome, dedupeKeyFor } from "./rawFeed";
import type { SignalCandidate } from "./gate";

// The reason strings below are copied verbatim from gate.ts / riskEngine.ts —
// if either changes its wording, these tests are the tripwire.

describe("classifyGateOutcome — passed", () => {
  it("marks a fully-cleared candidate GENERATED with no blocking layer", () => {
    expect(classifyGateOutcome("generated")).toEqual({ verdict: "GENERATED", blockedBy: null });
  });
});

describe("classifyGateOutcome — pre-gate skips", () => {
  const cases: Array<[string, string]> = [
    ["idempotent_duplicate", "duplicate"],
    ["cooldown_active", "cooldown"],
    ["insufficient_candles=7", "insufficient_candles"],
    ["ai_service_500: upstream boom", "ai_unreachable"],
    ["ai_service_unreachable: fetch failed", "ai_unreachable"],
  ];
  for (const [reason, blockedBy] of cases) {
    it(`tags "${reason}" as ${blockedBy}`, () => {
      expect(classifyGateOutcome("skipped", reason)).toEqual({ verdict: "SKIPPED", blockedBy });
    });
  }
});

describe("classifyGateOutcome — AI layer", () => {
  it("separates a low score from an explicit non-approval", () => {
    expect(classifyGateOutcome("rejected", "ai_score_too_low score=42")).toEqual({
      verdict: "REJECTED",
      blockedBy: "ai_score",
    });
    expect(classifyGateOutcome("rejected", "ai_not_approved: choppy structure")).toEqual({
      verdict: "REJECTED",
      blockedBy: "ai_judgment",
    });
  });
});

describe("classifyGateOutcome — regime layer", () => {
  it("tags the runner's pre-gate marker", () => {
    expect(classifyGateOutcome("rejected", "pre_gated_regime")).toEqual({
      verdict: "REJECTED",
      blockedBy: "regime",
    });
  });
});

describe("classifyGateOutcome — risk engine sub-layers", () => {
  const cases: Array<[string, string]> = [
    ["risk_rejected: entryPrice and stopLoss must differ", "risk_inputs"],
    ["risk_rejected: accountBalance must be a positive number", "risk_inputs"],
    ["risk_rejected: Daily loss limit reached", "risk_daily_loss"],
    ["risk_rejected: Max drawdown exceeded", "risk_drawdown"],
    ["risk_rejected: Risk/reward 1.20 below minimum 2", "risk_rr"],
    ["risk_rejected: Inside news window: US CPI", "risk_news"],
    ["risk_rejected: Inside high-impact news window", "risk_news"],
    ["risk_rejected: Gold concurrent limit: 3/3 positions already open", "risk_gold"],
    ["risk_rejected: something new nobody mapped", "risk"],
  ];
  for (const [reason, blockedBy] of cases) {
    it(`tags "${reason}" as ${blockedBy}`, () => {
      expect(classifyGateOutcome("rejected", reason)).toEqual({ verdict: "REJECTED", blockedBy });
    });
  }

  it("reports the FIRST reason the engine recorded when several fire at once", () => {
    // validateTrade pushes in order: inputs → daily loss → drawdown → RR → news.
    const joined = "risk_rejected: Daily loss limit reached; Risk/reward 1.20 below minimum 2";
    expect(classifyGateOutcome("rejected", joined).blockedBy).toBe("risk_daily_loss");
  });
});

describe("classifyGateOutcome — unmapped", () => {
  it("falls back to unknown rather than guessing a layer", () => {
    expect(classifyGateOutcome("skipped", "brand_new_reason")).toEqual({
      verdict: "SKIPPED",
      blockedBy: "unknown",
    });
    expect(classifyGateOutcome("skipped")).toEqual({ verdict: "SKIPPED", blockedBy: "unknown" });
  });
});

const CAND: SignalCandidate = {
  strategyName: "sweep_mss",
  symbol: "XAUUSD",
  timeframe: "60min",
  direction: "LONG",
  entryPrice: 2400.5,
  stopLoss: 2395.5,
  takeProfit: 2412.5,
  confidence: 60,
  reasoning: "sweep + MSS",
};

describe("dedupeKeyFor", () => {
  const now = new Date("2026-08-28T12:00:00Z");

  it("prefers the strategy's own per-bar clientId", () => {
    expect(dedupeKeyFor({ ...CAND, clientId: "bar-123" }, now)).toBe("cid:bar-123");
  });

  it("collapses an identical re-proposal on the same UTC day", () => {
    const a = dedupeKeyFor(CAND, now);
    const b = dedupeKeyFor({ ...CAND }, new Date("2026-08-28T23:59:00Z"));
    expect(a).toBe(b);
  });

  it("separates different levels, and the same setup on a later day", () => {
    expect(dedupeKeyFor({ ...CAND, takeProfit: 2420 }, now)).not.toBe(dedupeKeyFor(CAND, now));
    expect(dedupeKeyFor(CAND, new Date("2026-08-29T00:00:00Z"))).not.toBe(dedupeKeyFor(CAND, now));
  });
});

// The raw feed's whole safety argument is that nothing on the money path can see
// it. This test fails the build if that ever stops being true.
describe("raw feed is unreachable from execution", () => {
  it("is not imported by any module under execution/", () => {
    const dir = join(__dirname, "..", "execution");
    const offenders: string[] = [];
    const walk = (d: string): void => {
      for (const e of readdirSync(d, { withFileTypes: true })) {
        const full = join(d, e.name);
        if (e.isDirectory()) walk(full);
        else if (e.name.endsWith(".ts") && !e.name.endsWith(".test.ts")) {
          const src = readFileSync(full, "utf8");
          if (/from\s+["'][^"']*rawFeed["']|prisma\.rawSignal/.test(src)) offenders.push(e.name);
        }
      }
    };
    walk(dir);
    expect(offenders).toEqual([]);
  });
});
