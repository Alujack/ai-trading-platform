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

/** Full in-progress OHLCV bar pushed by the Python market-data worker. */
export interface RealtimeCandle {
  symbol: string;
  timeframe: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  timestamp: string;
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

// ---------------------------------------------------------------------------
// Raw ("layers off") strategy feed
// ---------------------------------------------------------------------------
// A RawSignal is the strategy's proposal exactly as it was emitted, recorded
// before any protection layer ran, with the layer verdict attached instead of
// applied. Observe-only: these rows have no path to a trade.

export type RawVerdict = "PENDING" | "GENERATED" | "REJECTED" | "SKIPPED";

export interface RawSignal {
  id: string;
  symbol: string;
  timeframe: string;
  direction: SignalDirection;
  entryPrice: string;
  stopLoss: string;
  takeProfit: string;
  /** The strategy's own confidence, never an AI score. */
  confidence: number;
  reasoning: string;
  strategyName: string;
  verdict: RawVerdict;
  /** Machine tag of the first layer that stopped it; null when it passed. */
  blockedBy: string | null;
  blockedReason: string | null;
  /** Set only when it cleared every layer and became a real Signal. */
  signalId: string | null;
  dedupeKey: string;
  seenCount: number;
  lastSeenAt: string;
  createdAt: string;
}

export interface RawSignalsResponse {
  data: RawSignal[];
  feedEnabled: boolean;
  pagination: { limit: number; offset: number; total: number };
}

export interface FeatureFlagState {
  key: string;
  enabled: boolean;
  source: "db" | "env" | "default";
}

/** FULL = every filter on, STRATEGY_ONLY = none, MIXED = some. */
export type LayerMode = "FULL" | "STRATEGY_ONLY" | "MIXED";

export interface GateLayer {
  key: string;
  label: string;
  enabled: boolean;
  appliedBy: "gate" | "strategy";
  /** Strategy param the worker overrides when this layer is off, if any. */
  param: string | null;
  offMeans: string;
}

export interface LayersResponse {
  mode: LayerMode;
  layers: GateLayer[];
  /** Checks no switch can reach — risk engine, breakers, caps, freshness. */
  mandatory: string[];
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

export interface AiProviderDetail {
  name: AiProvider;
  label: string;
  needsKey: boolean;
  hasKey: boolean;
  keyHint: string | null;
  keySource: "ui" | "env" | null;
  model: string | null;
  configured: boolean;
  active: boolean;
}

export interface AiProviderState {
  active: AiProvider;
  available: AiProvider[];
  providers: AiProviderDetail[];
}

export type Impact = "LOW" | "MEDIUM" | "HIGH";

export interface NewsItem {
  id: string;
  title: string;
  impact: Impact;
  currency: string;
  scheduledAt: string;
  actual: string | null;
  forecast: string | null;
  previous: string | null;
  aiSummary: string | null;
  upcoming: boolean;
}

export interface NewsResponse {
  data: NewsItem[];
  count: number;
}

export interface Position {
  id: string;
  symbol: string;
  direction: SignalDirection;
  size: number;
  entry: number;
  mark: number;
  stopLoss: number;
  takeProfit: number;
  pnl: number;
  openedAt: string;
}

export interface AccountSummary {
  baseBalance: number;
  equity: number;
  unrealized: number;
  realizedTotal: number;
  dayPnL: number;
  dayPnLPct: number;
  openRisk: number;
  openRiskPct: number;
  openCount: number;
  maxOpen: number;
}

export interface PositionsResponse {
  account: AccountSummary;
  positions: Position[];
}

export interface JournalEntry {
  id: string;
  notes: string;
  aiReview: string;
  emotions: string | null;
  createdAt: string;
  symbol: string;
  direction: SignalDirection;
  strategyName: string | null;
  status: string;
  profitLoss: number | null;
  closedAt: string | null;
}

export interface JournalResponse {
  data: JournalEntry[];
  count: number;
}

// ---- Control layer (risk config + execution modes) ----

export type ExecutionMode = "OFF" | "AUTO" | "CONFIRM";
export type ConfigScope = "GLOBAL" | "STRATEGY" | "SYMBOL";

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

export interface RiskBound {
  min: number;
  max: number;
  int?: boolean;
}

export interface RiskConfigResponse {
  effective: EffectiveRiskConfig;
  rows: Array<{ scope: ConfigScope; scopeKey: string; enabled: boolean }>;
  bounds: Record<keyof EffectiveRiskConfig, RiskBound>;
}

export interface ExecutionMapResponse {
  global: ExecutionMode;
  rows: Array<{ scope: ConfigScope; scopeKey: string; mode: ExecutionMode }>;
}

// ---- Telegram bridge config (UI-pasted credentials) ----

export type ConfigFieldSource = "ui" | "env" | "none";

export interface TelegramStatus {
  configured: boolean;
  hasToken: boolean;
  tokenHint: string | null;
  chatId: string | null;
  allowedUserIds: string[];
  hasWebhookSecret: boolean;
  sources: {
    botToken: ConfigFieldSource;
    chatId: ConfigFieldSource;
    webhookSecret: ConfigFieldSource;
    allowedUserIds: ConfigFieldSource;
  };
  webhook?: { url: string; pending: number; lastError?: string } | null;
}

// ---- Backtests ----

// One result row (per strategy × symbol × timeframe). Mirrors the Python
// Metrics dataclass plus the `verdict` string baked in when the run is saved.
export interface BacktestMetric {
  strategy: string;
  symbol: string;
  timeframe: string;
  bars_tested: number;
  signals_generated: number;
  trades: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate: number;
  gross_profit: number;
  gross_loss: number;
  net_pnl: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
  expectancy_r: number;
  payoff_ratio: number;
  starting_balance: number;
  ending_balance: number;
  return_pct: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  max_consecutive_losses: number;
  avg_hold_bars: number;
  total_costs: number;
  sharpe_per_trade: number;
  eod_closed: number;
  verdict: string;
}

export interface BacktestRunConfig {
  timeframes: string[];
  symbols: string[];
  strategies: string[];
  spread: number | null;
  slippage: number | null;
  commissionBps: number | null;
}

export interface BacktestRunSummary {
  id: string;
  label: string | null;
  startingBalance: number;
  riskPct: number;
  costsApplied: boolean;
  config: BacktestRunConfig;
  results: BacktestMetric[];
  createdAt: string;
}

export interface BacktestRunsResponse {
  runs: BacktestRunSummary[];
}

// [isoTimestamp, equity] points, keyed by "strategy|symbol|timeframe".
export type EquityCurves = Record<string, [string, number][]>;

export interface BacktestRunDetail extends BacktestRunSummary {
  equityCurves: EquityCurves;
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
