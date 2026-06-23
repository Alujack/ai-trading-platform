import { z } from "zod";

export const TIMEFRAMES = ["1min", "5min", "15min", "60min", "daily"] as const;

export const marketContextQuerySchema = z.object({
  symbol: z.string().min(1).max(20),
  timeframe: z.enum(TIMEFRAMES).default("60min"),
});

export type MarketContextQuery = z.infer<typeof marketContextQuerySchema>;
