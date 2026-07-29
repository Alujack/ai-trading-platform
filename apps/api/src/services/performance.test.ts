import { describe, expect, it } from "vitest";
import {
  computePerformance,
  computeStrategyDrift,
  type DriftTradeStats,
  type TradeStats,
} from "./performance";

const winLong: TradeStats = {
  entryPrice: 100,
  exitPrice: 104,
  profitLoss: 20,
  direction: "LONG",
  stopLoss: 98,
};
const lossLong: TradeStats = {
  entryPrice: 100,
  exitPrice: 98,
  profitLoss: -10,
  direction: "LONG",
  stopLoss: 98,
};
const winShort: TradeStats = {
  entryPrice: 100,
  exitPrice: 96,
  profitLoss: 20,
  direction: "SHORT",
  stopLoss: 102,
};

describe("computePerformance", () => {
  it("returns all zeros for an empty trade list", () => {
    expect(computePerformance([])).toEqual({
      totalTrades: 0,
      winRate: 0,
      totalPnL: 0,
      maxDrawdown: 0,
      averageRR: 0,
      expectancy: 0,
      profitFactor: 0,
    });
  });

  it("computes expectancy (avg P&L per trade) and profit factor", () => {
    // winLong +20, lossLong -10 → expectancy (20-10)/2 = 5; PF 20/10 = 2
    const r = computePerformance([winLong, lossLong]);
    expect(r.expectancy).toBe(5);
    expect(r.profitFactor).toBe(2);
  });

  it("reports Infinity profit factor when there are no losses", () => {
    expect(computePerformance([winLong]).profitFactor).toBe(Infinity);
  });

  it("computes win rate, totalPnL and averageRR for a single winning LONG", () => {
    const r = computePerformance([winLong]);
    expect(r.totalTrades).toBe(1);
    expect(r.winRate).toBe(100);
    expect(r.totalPnL).toBe(20);
    expect(r.averageRR).toBe(2); // (104-100)/(100-98) = 2
    expect(r.maxDrawdown).toBe(0); // no trough after the peak
  });

  it("computes a negative averageRR for a loss with full risk taken", () => {
    const r = computePerformance([lossLong]);
    expect(r.winRate).toBe(0);
    expect(r.averageRR).toBe(-1); // exit hit SL exactly
  });

  it("mirrors RR sign correctly for SHORT trades", () => {
    const r = computePerformance([winShort]);
    expect(r.averageRR).toBe(2); // (100-96)/|100-102| = 2
  });

  it("averages RR across mixed trades", () => {
    const r = computePerformance([winLong, lossLong]);
    // (2 + -1) / 2 = 0.5
    expect(r.averageRR).toBe(0.5);
    expect(r.winRate).toBe(50);
    expect(r.totalPnL).toBe(10);
  });

  it("calculates maxDrawdown as the largest peak-to-trough drop in cumulative P&L", () => {
    // Cumulative: 20, 10, -5, 15 -> peak 20, trough -5 -> drawdown 25
    const seq: TradeStats[] = [
      { ...winLong, profitLoss: 20 },
      { ...lossLong, profitLoss: -10 },
      { ...lossLong, profitLoss: -15 },
      { ...winLong, profitLoss: 20 },
    ];
    const r = computePerformance(seq);
    expect(r.totalPnL).toBe(15);
    expect(r.maxDrawdown).toBe(25);
  });

  it("ignores RR contribution from trades with null exit price", () => {
    const stillOpen: TradeStats = { ...winLong, exitPrice: null, profitLoss: 0 };
    const r = computePerformance([stillOpen, winLong]);
    expect(r.averageRR).toBe(2); // only the closed trade contributes
  });

  it("ignores RR contribution when risk is zero (entry==stop)", () => {
    const degenerate: TradeStats = {
      entryPrice: 100,
      exitPrice: 105,
      profitLoss: 5,
      direction: "LONG",
      stopLoss: 100,
    };
    expect(computePerformance([degenerate]).averageRR).toBe(0);
  });

  it("treats null profitLoss as zero for cumulative metrics", () => {
    const noPnl: TradeStats = { ...winLong, profitLoss: null };
    const r = computePerformance([noPnl]);
    expect(r.totalPnL).toBe(0);
    expect(r.winRate).toBe(0); // null P&L doesn't count as a win
  });
});

// --------------------------------------------------------------------------- //

const DAY = 86_400_000;

/** n trades for `strategyName` at a fixed confidence, `wins` of them profitable. */
function batch(
  strategyName: string | null,
  confidenceScore: number,
  n: number,
  wins: number,
  startDay = 0,
): DriftTradeStats[] {
  return Array.from({ length: n }, (_, i) => ({
    strategyName,
    confidenceScore,
    profitLoss: i < wins ? 10 : -10,
    closedAt: new Date(Date.UTC(2026, 0, 1) + (startDay + i) * DAY),
  }));
}

describe("computeStrategyDrift", () => {
  it("returns an empty list when there is nothing to score", () => {
    expect(computeStrategyDrift([])).toEqual([]);
  });

  it("groups by strategy and puts the biggest sample first", () => {
    const r = computeStrategyDrift([
      ...batch("ict_confluence", 70, 10, 6),
      ...batch("ml_xau", 60, 3, 1),
    ]);
    expect(r.map((s) => s.strategyName)).toEqual(["ict_confluence", "ml_xau"]);
    expect(r[0].trades).toBe(10);
    expect(r[0].winRate).toBe(60);
    expect(r[0].meanConfidence).toBe(70);
  });

  it("excludes unresolved trades — an open trade cannot score a prediction", () => {
    const open: DriftTradeStats = {
      strategyName: "ict_confluence",
      confidenceScore: 80,
      profitLoss: null,
      closedAt: null,
    };
    const r = computeStrategyDrift([...batch("ict_confluence", 70, 4, 2), open]);
    expect(r[0].trades).toBe(4);
  });

  it("buckets null strategyName rather than dropping it", () => {
    const r = computeStrategyDrift(batch(null, 50, 2, 1));
    expect(r[0].strategyName).toBe("(unattributed)");
  });

  it("scores discrimination positive when confidence ranks trades correctly", () => {
    // 30s bucket wins 20%, 80s bucket wins 90% → the score is informative.
    const r = computeStrategyDrift([
      ...batch("ml_xau", 35, 10, 2),
      ...batch("ml_xau", 85, 10, 9),
    ]);
    expect(r[0].buckets.map((b) => b.bucket)).toEqual(["30-39", "80-89"]);
    expect(r[0].discrimination).toBe(70); // 90 - 20
  });

  it("scores discrimination negative when confidence is anti-predictive", () => {
    const r = computeStrategyDrift([
      ...batch("ml_xau", 35, 10, 9),
      ...batch("ml_xau", 85, 10, 2),
    ]);
    expect(r[0].discrimination).toBe(-70);
  });

  it("leaves discrimination null with only one populated decile", () => {
    expect(computeStrategyDrift(batch("ml_xau", 55, 8, 4))[0].discrimination).toBeNull();
  });

  it("reports drift when recent trades diverge from the lifetime rate", () => {
    // 20 old trades at 80% win, then 10 recent at 0% → recent window of 10
    // sees 0%, lifetime is 53.33%.
    const r = computeStrategyDrift(
      [...batch("ml_xau", 60, 20, 16, 0), ...batch("ml_xau", 60, 10, 0, 20)],
      10,
    );
    expect(r[0].trades).toBe(30);
    expect(r[0].winRate).toBe(53.33);
    expect(r[0].recentWinRate).toBe(0);
    expect(r[0].drift).toBe(-53.33); // the decay signal
  });

  it("leaves recent metrics null below the window size", () => {
    const r = computeStrategyDrift(batch("ml_xau", 60, 5, 3), 30);
    expect(r[0].recentWinRate).toBeNull();
    expect(r[0].drift).toBeNull();
  });

  it("counts breakeven in the denominator but not as a win", () => {
    const be: DriftTradeStats = {
      strategyName: "ml_xau",
      confidenceScore: 60,
      profitLoss: 0,
      closedAt: new Date(Date.UTC(2026, 0, 1)),
    };
    const r = computeStrategyDrift([...batch("ml_xau", 60, 1, 1), be]);
    expect(r[0].trades).toBe(2);
    expect(r[0].winRate).toBe(50);
  });
});
