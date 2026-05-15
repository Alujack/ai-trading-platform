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

export type SignalsQuery = z.infer<typeof signalsQuerySchema>;
export type SignalIdParam = z.infer<typeof signalIdParamSchema>;
