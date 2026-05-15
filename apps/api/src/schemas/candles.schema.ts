import { z } from "zod";

// Labels match what services/data writes to Candle.timeframe (Twelve Data
// interval naming). Keep these in sync with TIMEFRAME_PERIOD_SECONDS in
// services/data/main.py if either side changes.
export const TIMEFRAMES = ["1min", "5min", "15min", "60min", "daily"] as const;

export const candlesQuerySchema = z.object({
  symbol: z.string().min(1).max(20),
  timeframe: z.enum(TIMEFRAMES),
  limit: z.coerce.number().int().min(1).max(1000).default(100),
});

export type CandlesQuery = z.infer<typeof candlesQuerySchema>;
