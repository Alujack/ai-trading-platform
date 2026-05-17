import { Router } from "express";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";

const router = Router();

interface PerformanceResponse {
  totalTrades: number;
  winRate: number;
  totalPnL: number;
  maxDrawdown: number;
  averageRR: number;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

router.get(
  "/performance",
  asyncHandler(async (_req, res) => {
    const trades = await prisma.trade.findMany({
      where: { status: "CLOSED" },
      include: { signal: { select: { direction: true, stopLoss: true } } },
      orderBy: { closedAt: "asc" },
    });

    let totalPnL = 0;
    let wins = 0;
    let rrSum = 0;
    let rrCount = 0;
    let runningPnL = 0;
    let peakPnL = 0;
    let maxDrawdown = 0;

    for (const t of trades) {
      const pnl = t.profitLoss ? Number(t.profitLoss.toString()) : 0;
      totalPnL += pnl;
      if (pnl > 0) wins += 1;

      const entry = Number(t.entryPrice.toString());
      const exit = t.exitPrice ? Number(t.exitPrice.toString()) : null;
      const stop = Number(t.signal.stopLoss.toString());
      if (exit !== null) {
        const risk = Math.abs(entry - stop);
        if (risk > 0) {
          const reward = t.signal.direction === "LONG" ? exit - entry : entry - exit;
          rrSum += reward / risk;
          rrCount += 1;
        }
      }

      runningPnL += pnl;
      if (runningPnL > peakPnL) peakPnL = runningPnL;
      const dd = peakPnL - runningPnL;
      if (dd > maxDrawdown) maxDrawdown = dd;
    }

    const body: PerformanceResponse = {
      totalTrades: trades.length,
      winRate: trades.length > 0 ? round2((wins / trades.length) * 100) : 0,
      totalPnL: round2(totalPnL),
      maxDrawdown: round2(maxDrawdown),
      averageRR: rrCount > 0 ? round2(rrSum / rrCount) : 0,
    };
    res.json(body);
  }),
);

export default router;
