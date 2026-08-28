import { z } from "zod";

export const SIGNAL_STATUSES = ["PENDING", "ACTIVE", "CLOSED", "CANCELLED"] as const;

export const signalsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(200).default(50),
  offset: z.coerce.number().int().min(0).default(0),
  status: z.enum(SIGNAL_STATUSES).optional(),
  symbol: z.string().min(1).max(20).optional(),
});

export const signalIdParamSchema = z.object({
  id: z.string().min(1),
});

export const TIMEFRAMES = ["1min", "5min", "15min", "60min", "daily"] as const;

export const signalCandidateSchema = z.object({
  strategyName: z.string().min(1).max(50),
  symbol: z.string().min(1).max(20),
  timeframe: z.enum(TIMEFRAMES),
  direction: z.enum(["LONG", "SHORT"]),
  entryPrice: z.number().finite().positive(),
  stopLoss: z.number().finite().positive(),
  takeProfit: z.number().finite().positive(),
  confidence: z.number().min(0).max(100),
  reasoning: z.string().min(1).max(4000),
  clientId: z.string().min(1).max(64).optional(),
  cooldownMs: z.number().int().nonnegative().max(604_800_000).optional(),
  aiMinScore: z.number().min(0).max(100).optional(),
  // Raw-feed only: an upstream layer already refused this candidate, so the gate
  // records it and rejects it without evaluating. Never becomes a Signal.
  preGatedBy: z.enum(["regime"]).optional(),
});

export const RAW_VERDICTS = ["PENDING", "GENERATED", "REJECTED", "SKIPPED"] as const;

export const rawSignalsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(200).default(50),
  offset: z.coerce.number().int().min(0).default(0),
  symbol: z.string().min(1).max(20).optional(),
  strategy: z.string().min(1).max(50).optional(),
  timeframe: z.enum(TIMEFRAMES).optional(),
  verdict: z.enum(RAW_VERDICTS).optional(),
  /** "1"/"true" = only candidates a layer stopped (what automation did NOT take).
   *  Kept as a string enum, not a transform: the validate() middleware requires a
   *  schema whose input and output types match. */
  blockedOnly: z.enum(["0", "1", "true", "false"]).optional(),
});

/** Did the caller ask for blocked-only? */
export function isBlockedOnly(v: RawSignalsQuery["blockedOnly"]): boolean {
  return v === "1" || v === "true";
}

export type SignalsQuery = z.infer<typeof signalsQuerySchema>;
export type RawSignalsQuery = z.infer<typeof rawSignalsQuerySchema>;
export type SignalIdParam = z.infer<typeof signalIdParamSchema>;
export type SignalCandidateBody = z.infer<typeof signalCandidateSchema>;
