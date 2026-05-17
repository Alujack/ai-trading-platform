import { describe, expect, it } from "vitest";
import { computePerformance, type TradeStats } from "./performance";

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
    });
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
