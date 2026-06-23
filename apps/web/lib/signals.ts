import type { Signal } from "./types";

// One pip in price units, per symbol.
const PIP_SIZE: Record<string, number> = {
  XAUUSD: 0.1,
  EURUSD: 0.0001,
  BTCUSD: 1,
};

export function pipSize(symbol: string): number {
  return PIP_SIZE[symbol] ?? 0.0001;
}

export function pipsBetween(symbol: string, a: number, b: number): number {
  return Math.abs(a - b) / pipSize(symbol);
}

// Approx P&L per pip at 0.01 lot (micro), in USD.
const PIP_VALUE_001_LOT: Record<string, number> = {
  XAUUSD: 0.1, // 1 oz, $0.10 pip
  EURUSD: 0.1, // 1,000 units, $0.10 pip
  BTCUSD: 0.01, // 0.01 BTC, $1 pip
};

export function usdAt001Lot(symbol: string, pips: number): number {
  return pips * (PIP_VALUE_001_LOT[symbol] ?? 0.1);
}

/** Prefer an open idea (PENDING/ACTIVE); otherwise the most recent signal. */
export function pickActiveSignal(signals: Signal[]): Signal | null {
  if (signals.length === 0) return null;
  const open = signals.find((s) => s.status === "PENDING" || s.status === "ACTIVE");
  return open ?? signals[0] ?? null;
}

export function riskReward(entry: number, stop: number, target: number): number {
  const risk = Math.abs(entry - stop);
  return risk > 0 ? Math.abs(target - entry) / risk : 0;
}
