import { Router } from "express";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";
import {
  computePerformance,
  computeStrategyDrift,
  DEFAULT_RECENT_WINDOW,
  type DriftTradeStats,
  type TradeStats,
} from "../services/performance";

const router = Router();

router.get(
  "/performance",
  asyncHandler(async (_req, res) => {
    const trades = await prisma.trade.findMany({
      where: { status: "CLOSED" },
      include: { signal: { select: { direction: true, stopLoss: true } } },
      orderBy: { closedAt: "asc" },
    });

    const stats: TradeStats[] = trades.map((t) => ({
      entryPrice: Number(t.entryPrice.toString()),
      exitPrice: t.exitPrice ? Number(t.exitPrice.toString()) : null,
      profitLoss: t.profitLoss ? Number(t.profitLoss.toString()) : null,
      direction: t.signal.direction,
      stopLoss: Number(t.signal.stopLoss.toString()),
    }));

    res.json(computePerformance(stats));
  }),
);

/**
 * Per-strategy model drift — did the confidence the model claimed match what
 * actually happened? See `computeStrategyDrift` for how to read the numbers
 * (short version: trust `discrimination` and `drift`, not the raw gap between
 * `meanConfidence` and `winRate`).
 *
 * `?window=N` sets the trailing-trade count used for the recent-vs-lifetime
 * comparison. Clamped to 5..500 so a caller cannot ask for a window of 1 and
 * read pure noise as a decay signal.
 */
router.get(
  "/performance/drift",
  asyncHandler(async (req, res) => {
    const raw = Number(req.query.window);
    const window = Number.isFinite(raw)
      ? Math.min(500, Math.max(5, Math.trunc(raw)))
      : DEFAULT_RECENT_WINDOW;

    const trades = await prisma.trade.findMany({
      where: { status: "CLOSED" },
      include: { signal: { select: { strategyName: true, confidenceScore: true } } },
      orderBy: { closedAt: "asc" },
    });

    const stats: DriftTradeStats[] = trades.map((t) => ({
      strategyName: t.signal.strategyName,
      confidenceScore: t.signal.confidenceScore,
      profitLoss: t.profitLoss ? Number(t.profitLoss.toString()) : null,
      closedAt: t.closedAt,
    }));

    res.json({ window, strategies: computeStrategyDrift(stats, window) });
  }),
);

export default router;
