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
});

export type SignalsQuery = z.infer<typeof signalsQuerySchema>;
export type SignalIdParam = z.infer<typeof signalIdParamSchema>;
export type SignalCandidateBody = z.infer<typeof signalCandidateSchema>;
