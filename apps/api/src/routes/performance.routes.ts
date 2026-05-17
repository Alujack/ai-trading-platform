import { Router } from "express";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";
import { computePerformance, type TradeStats } from "../services/performance";

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

export default router;
