/**
 * Code-level defaults for every runtime-configurable risk parameter. These are
 * the seed/fallback values: a resolved config field falls through
 * SYMBOL ► STRATEGY ► GLOBAL and finally lands here if nothing is set. They
 * mirror the constants that used to live in riskEngine.ts / gate.ts so the
 * system behaves identically on day one until something is changed in the UI.
 */
export interface EffectiveRiskConfig {
  riskPerTradePct: number;
  minRR: number;
  dailyLossLimitPct: number;
  maxDrawdownPct: number;
  maxOpenTrades: number;
  maxOpenRiskPct: number;
  maxRiskPerCurrencyPct: number;
  newsBeforeMin: number;
  newsAfterMin: number;
  aiMinScore: number;
  approvalTtlMin: number;
}

export const RISK_DEFAULTS: EffectiveRiskConfig = {
  riskPerTradePct: Number(process.env.PAPER_RISK_PERCENT ?? "1"),
  minRR: 2,
  dailyLossLimitPct: 3,
  maxDrawdownPct: 10,
  maxOpenTrades: Number(process.env.PAPER_MAX_OPEN_TRADES ?? "5"),
  maxOpenRiskPct: 5,
  // All instruments here are USD-quoted, so a tight per-currency cap throttles
  // everything at once. Keep it level with the open-risk cap by default.
  maxRiskPerCurrencyPct: 5,
  newsBeforeMin: 30,
  newsAfterMin: 30,
  aiMinScore: 70,
  approvalTtlMin: 15,
};

/** Hard bounds the API enforces so the UI can never set a self-destructive value. */
export const RISK_BOUNDS: Record<keyof EffectiveRiskConfig, { min: number; max: number; int?: boolean }> = {
  riskPerTradePct: { min: 0.01, max: 5 },
  minRR: { min: 1, max: 10 },
  dailyLossLimitPct: { min: 0.1, max: 50 },
  maxDrawdownPct: { min: 0.1, max: 100 },
  maxOpenTrades: { min: 1, max: 100, int: true },
  maxOpenRiskPct: { min: 0.1, max: 100 },
  maxRiskPerCurrencyPct: { min: 0.1, max: 100 },
  newsBeforeMin: { min: 0, max: 240, int: true },
  newsAfterMin: { min: 0, max: 240, int: true },
  aiMinScore: { min: 0, max: 100, int: true },
  approvalTtlMin: { min: 1, max: 1440, int: true },
};

export type Scope = "GLOBAL" | "STRATEGY" | "SYMBOL";
export const SCOPES: Scope[] = ["GLOBAL", "STRATEGY", "SYMBOL"];

/** Static currency map for per-currency exposure caps (Part B §3.4.3). */
export const SYMBOL_CURRENCIES: Record<string, string[]> = {
  XAUUSD: ["XAU", "USD"],
  EURUSD: ["EUR", "USD"],
  BTCUSD: ["BTC", "USD"],
};
