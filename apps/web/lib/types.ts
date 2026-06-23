export const TIMEFRAMES = ["1min", "5min", "15min", "60min", "daily"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export const SYMBOLS = ["XAUUSD", "EURUSD", "BTCUSD"] as const;
export type Symbol = (typeof SYMBOLS)[number];

export interface Indicators {
  rsi: string | null;
  ema20: string | null;
  ema50: string | null;
  ema200: string | null;
  atr: string | null;
}

export interface Candle {
  id: string;
  symbol: string;
  timeframe: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  timestamp: string;
  createdAt: string;
  indicators: Indicators | null;
}

export type SignalDirection = "LONG" | "SHORT";
export type SignalStatus = "PENDING" | "ACTIVE" | "CLOSED" | "CANCELLED";

export interface Signal {
  id: string;
  symbol: string;
  timeframe: string;
  direction: SignalDirection;
  entryPrice: string;
  stopLoss: string;
  takeProfit: string;
  confidenceScore: number;
  aiReasoning: string;
  strategyName: string | null;
  status: SignalStatus;
  createdAt: string;
}

export interface SignalsResponse {
  data: Signal[];
  pagination: { limit: number; offset: number; total: number };
}

export interface Performance {
  totalTrades: number;
  winRate: number;
  totalPnL: number;
  maxDrawdown: number;
  averageRR: number;
}

export const AI_PROVIDERS = ["mock", "anthropic", "gemini"] as const;
export type AiProvider = (typeof AI_PROVIDERS)[number];

export interface AiProviderState {
  active: AiProvider;
  available: AiProvider[];
}

export type MarketBias = "Bullish" | "Bearish" | "Neutral";

export interface MarketContext {
  symbol: string;
  timeframe: string;
  bias: MarketBias;
  summary: string;
  keyLevels: string[];
  risks: string[];
  generatedAt: string;
  cached: boolean;
}
