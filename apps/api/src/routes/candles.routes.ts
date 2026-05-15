import { Router } from "express";
import { prisma } from "../lib/prisma";
import { ser } from "../lib/decimal";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";
import { candlesQuerySchema, type CandlesQuery } from "../schemas/candles.schema";

const router = Router();

router.get(
  "/candles",
  validate(candlesQuerySchema, "query"),
  asyncHandler(async (req, res) => {
    const { symbol, timeframe, limit } = req.query as unknown as CandlesQuery;

    const candles = await prisma.candle.findMany({
      where: { symbol, timeframe },
      orderBy: { timestamp: "desc" },
      take: limit,
    });

    if (candles.length === 0) {
      return res.json([]);
    }

    const indicators = await prisma.indicator.findMany({
      where: {
        symbol,
        timeframe,
        timestamp: { in: candles.map((c) => c.timestamp) },
      },
    });

    const byTs = new Map(indicators.map((i) => [i.timestamp.toISOString(), i]));

    const merged = candles.map((c) => {
      const ind = byTs.get(c.timestamp.toISOString());
      return {
        ...c,
        indicators: ind
          ? {
              rsi: ind.rsi,
              ema20: ind.ema20,
              ema50: ind.ema50,
              ema200: ind.ema200,
              atr: ind.atr,
            }
          : null,
      };
    });

    res.json(ser(merged));
  }),
);

export default router;
