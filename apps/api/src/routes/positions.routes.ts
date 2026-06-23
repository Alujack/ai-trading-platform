import { Router } from "express";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";

const router = Router();

const BASE_BALANCE = Number(process.env.PAPER_ACCOUNT_BALANCE ?? "10000");
const MAX_OPEN = Number(process.env.PAPER_MAX_OPEN_TRADES ?? "5");

function num(v: { toString(): string } | null | undefined): number {
  return v == null ? 0 : Number(v.toString());
}

// positionSize from the gate = riskAmount / stopDistance, so $PnL is just
// priceDifference * positionSize — consistent with the risk model.
function pnl(direction: string, entry: number, mark: number, size: number): number {
  const dir = direction === "LONG" ? 1 : -1;
  return dir * (mark - entry) * size;
}

// Open positions (live, marked to the latest candle) + an account summary that
// feeds the dashboard KPI strip and the top-bar equity.
router.get(
  "/positions",
  asyncHandler(async (_req, res) => {
    const openTrades = await prisma.trade.findMany({
      where: { status: "OPEN" },
      include: {
        signal: {
          select: { symbol: true, direction: true, stopLoss: true, takeProfit: true },
        },
      },
      orderBy: { openedAt: "desc" },
    });

    // Latest mark price per symbol that has an open position.
    const symbols = [...new Set(openTrades.map((t) => t.signal.symbol))];
    const marks = new Map<string, number>();
    await Promise.all(
      symbols.map(async (symbol) => {
        const last = await prisma.candle.findFirst({
          where: { symbol },
          orderBy: { timestamp: "desc" },
          select: { close: true },
        });
        if (last) marks.set(symbol, num(last.close));
      }),
    );

    let unrealized = 0;
    let openRisk = 0;
    const positions = openTrades.map((t) => {
      const entry = num(t.entryPrice);
      const size = num(t.positionSize);
      const mark = marks.get(t.signal.symbol) ?? entry;
      const upnl = pnl(t.signal.direction, entry, mark, size);
      unrealized += upnl;
      openRisk += num(t.riskAmount);
      return {
        id: t.id,
        symbol: t.signal.symbol,
        direction: t.signal.direction,
        size,
        entry,
        mark,
        stopLoss: num(t.signal.stopLoss),
        takeProfit: num(t.signal.takeProfit),
        pnl: Math.round(upnl * 100) / 100,
        openedAt: t.openedAt.toISOString(),
      };
    });

    // Realized P&L: all-time and since the start of today (UTC).
    const closed = await prisma.trade.findMany({
      where: { status: "CLOSED" },
      select: { profitLoss: true, closedAt: true },
    });
    const startOfDay = new Date();
    startOfDay.setUTCHours(0, 0, 0, 0);
    let realizedTotal = 0;
    let realizedToday = 0;
    for (const c of closed) {
      const p = num(c.profitLoss);
      realizedTotal += p;
      if (c.closedAt && c.closedAt >= startOfDay) realizedToday += p;
    }

    const equity = BASE_BALANCE + realizedTotal + unrealized;
    const round2 = (n: number) => Math.round(n * 100) / 100;

    res.json({
      account: {
        baseBalance: BASE_BALANCE,
        equity: round2(equity),
        unrealized: round2(unrealized),
        realizedTotal: round2(realizedTotal),
        dayPnL: round2(realizedToday + unrealized),
        dayPnLPct: round2(((realizedToday + unrealized) / BASE_BALANCE) * 100),
        openRisk: round2(openRisk),
        openRiskPct: round2((openRisk / equity) * 100),
        openCount: positions.length,
        maxOpen: MAX_OPEN,
      },
      positions,
    });
  }),
);

export default router;
