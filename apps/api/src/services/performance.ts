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
  // Expectancy = average P&L per trade (the real edge metric — what win rate
  // alone cannot tell you). Profit factor = gross profit ÷ gross loss (>1 = edge).
  expectancy: number;
  profitFactor: number;
}

/** One closed trade, joined to the confidence its signal was created with. */
export interface DriftTradeStats {
  strategyName: string | null;
  confidenceScore: number; // 0-100, as written to Signal
  profitLoss: number | null;
  closedAt: Date | string | null;
}

export interface ConfidenceBucket {
  /** Decile label, e.g. "60-69". */
  bucket: string;
  trades: number;
  winRate: number;
}

export interface StrategyDrift {
  strategyName: string;
  trades: number;
  /** Mean confidence the model claimed across these trades. */
  meanConfidence: number;
  /** Share of those trades that actually made money. */
  winRate: number;
  buckets: ConfidenceBucket[];
  /**
   * Win rate of the highest-confidence populated decile minus the lowest.
   * This is the question "is the score informative at all?" — positive means
   * the model ranks its own trades correctly, ~0 means the number is noise,
   * negative means it is anti-predictive. Null until two deciles are populated.
   */
  discrimination: number | null;
  /** Win rate over the trailing `recentWindow` trades. Null if too few. */
  recentWinRate: number | null;
  /**
   * recentWinRate - winRate. The decay signal: a model that was calibrated at
   * deployment and is now drifting shows up here before it shows up in P&L.
   */
  drift: number | null;
}

export const DEFAULT_RECENT_WINDOW = 30;

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * Per-strategy model drift: predicted confidence vs realized outcome.
 *
 * Replaces the idea behind the vendored `research/xaubot/python_monitoring/`,
 * which parsed a `prediction_log.csv` out of the MT5 Files folder — a file that
 * only exists on the MQL5 EA path we do not run. Every signal this platform
 * emits already carries `confidenceScore` and `strategyName` into Postgres, and
 * both columns are indexed, so the same question is answerable from data we own.
 *
 * A caveat worth keeping in view when reading the output: `confidenceScore` is
 * not a calibrated win probability. `ml_xau`, for instance, maps a model
 * probability onto an arbitrary 0-90 band. So `meanConfidence` vs `winRate` is
 * NOT a like-for-like comparison and a gap between them means little on its own.
 * `discrimination` and `drift` are the trustworthy signals here, because both
 * compare the score against itself rather than against an absolute scale.
 *
 * Trades with an unresolved `profitLoss` are excluded — an open trade has no
 * outcome to score the prediction against. Breakevens count in the denominator
 * but not as wins, matching `computePerformance`.
 */
export function computeStrategyDrift(
  trades: DriftTradeStats[],
  recentWindow: number = DEFAULT_RECENT_WINDOW,
): StrategyDrift[] {
  const byStrategy = new Map<string, DriftTradeStats[]>();
  for (const t of trades) {
    if (t.profitLoss === null) continue;
    const key = t.strategyName ?? "(unattributed)";
    const bucket = byStrategy.get(key);
    if (bucket) bucket.push(t);
    else byStrategy.set(key, [t]);
  }

  const out: StrategyDrift[] = [];
  for (const [strategyName, group] of byStrategy) {
    // Oldest first, so "recent" is the tail. Undated trades sort first: they
    // cannot be shown to be recent, and dropping them would silently shrink
    // the lifetime baseline the drift number is measured against.
    const ordered = [...group].sort((a, b) => time(a.closedAt) - time(b.closedAt));

    const wins = ordered.filter((t) => (t.profitLoss ?? 0) > 0).length;
    const winRate = (wins / ordered.length) * 100;
    const meanConfidence =
      ordered.reduce((sum, t) => sum + t.confidenceScore, 0) / ordered.length;

    // Deciles, ascending, populated only.
    const deciles = new Map<number, { trades: number; wins: number }>();
    for (const t of ordered) {
      const d = Math.min(9, Math.max(0, Math.floor(t.confidenceScore / 10)));
      const cell = deciles.get(d) ?? { trades: 0, wins: 0 };
      cell.trades += 1;
      if ((t.profitLoss ?? 0) > 0) cell.wins += 1;
      deciles.set(d, cell);
    }
    const buckets: ConfidenceBucket[] = [...deciles.entries()]
      .sort(([a], [b]) => a - b)
      .map(([d, cell]) => ({
        bucket: `${d * 10}-${d * 10 + 9}`,
        trades: cell.trades,
        winRate: round2((cell.wins / cell.trades) * 100),
      }));

    const discrimination =
      buckets.length >= 2
        ? round2(buckets[buckets.length - 1].winRate - buckets[0].winRate)
        : null;

    let recentWinRate: number | null = null;
    let drift: number | null = null;
    if (ordered.length >= recentWindow) {
      const tail = ordered.slice(-recentWindow);
      const tailWins = tail.filter((t) => (t.profitLoss ?? 0) > 0).length;
      recentWinRate = round2((tailWins / tail.length) * 100);
      drift = round2(recentWinRate - winRate);
    }

    out.push({
      strategyName,
      trades: ordered.length,
      meanConfidence: round2(meanConfidence),
      winRate: round2(winRate),
      buckets,
      discrimination,
      recentWinRate,
      drift,
    });
  }

  // Most-traded first — that is the one with enough sample to act on.
  return out.sort((a, b) => b.trades - a.trades);
}

function time(v: Date | string | null): number {
  if (v === null) return 0;
  return v instanceof Date ? v.getTime() : new Date(v).getTime();
}

export function computePerformance(trades: TradeStats[]): PerformanceResponse {
  let totalPnL = 0;
  let wins = 0;
  let rrSum = 0;
  let rrCount = 0;
  let runningPnL = 0;
  let peakPnL = 0;
  let maxDrawdown = 0;
  let grossProfit = 0;
  let grossLoss = 0;

  for (const t of trades) {
    const pnl = t.profitLoss ?? 0;
    totalPnL += pnl;
    if (pnl > 0) {
      wins += 1;
      grossProfit += pnl;
    } else if (pnl < 0) {
      grossLoss += Math.abs(pnl);
    }

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
    expectancy: trades.length > 0 ? round2(totalPnL / trades.length) : 0,
    profitFactor: grossLoss > 0 ? round2(grossProfit / grossLoss) : grossProfit > 0 ? Infinity : 0,
  };
}
