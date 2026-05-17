export interface TradeStats {
  entryPrice: number;
  exitPrice: number | null;
  profitLoss: number | null;
  direction: "LONG" | "SHORT";
  stopLoss: number;
}

export interface PerformanceResponse {
  totalTrades: number;
  winRate: number;
  totalPnL: number;
  maxDrawdown: number;
  averageRR: number;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export function computePerformance(trades: TradeStats[]): PerformanceResponse {
  let totalPnL = 0;
  let wins = 0;
  let rrSum = 0;
  let rrCount = 0;
  let runningPnL = 0;
  let peakPnL = 0;
  let maxDrawdown = 0;

  for (const t of trades) {
    const pnl = t.profitLoss ?? 0;
    totalPnL += pnl;
    if (pnl > 0) wins += 1;

    if (t.exitPrice !== null) {
      const risk = Math.abs(t.entryPrice - t.stopLoss);
      if (risk > 0) {
        const reward =
          t.direction === "LONG" ? t.exitPrice - t.entryPrice : t.entryPrice - t.exitPrice;
        rrSum += reward / risk;
        rrCount += 1;
      }
    }

    runningPnL += pnl;
    if (runningPnL > peakPnL) peakPnL = runningPnL;
    const dd = peakPnL - runningPnL;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  return {
    totalTrades: trades.length,
    winRate: trades.length > 0 ? round2((wins / trades.length) * 100) : 0,
    totalPnL: round2(totalPnL),
    maxDrawdown: round2(maxDrawdown),
    averageRR: rrCount > 0 ? round2(rrSum / rrCount) : 0,
  };
}
