import type { Candle, Symbol, Timeframe } from "./types";

const WEEKEND_MARKETS = new Set<Symbol>(["XAUUSD", "EURUSD"]);

const TIMEFRAME_MS: Record<Timeframe, number> = {
  "1min": 60_000,
  "5min": 5 * 60_000,
  "15min": 15 * 60_000,
  "60min": 60 * 60_000,
  daily: 24 * 60 * 60_000,
};

export type MarketDataState = "live" | "delayed" | "closed" | "missing";

export interface MarketStatus {
  state: MarketDataState;
  marketOpen: boolean;
  label: string;
  session: string;
  detail: string;
  latestAt: Date | null;
}

export interface CandleGap {
  start: Date;
  end: Date;
  durationMs: number;
}

export function isMarketOpen(symbol: Symbol, at = new Date()): boolean {
  if (!WEEKEND_MARKETS.has(symbol)) return true;
  const day = at.getUTCDay();
  const hour = at.getUTCHours();
  return !((day === 5 && hour >= 22) || day === 6 || (day === 0 && hour < 22));
}

export function marketSession(symbol: Symbol, at = new Date()): string {
  if (symbol === "BTCUSD") return "24 / 7 market";
  if (!isMarketOpen(symbol, at)) return "Weekend break";
  const hour = at.getUTCHours();
  if (hour < 7) return "Asia session";
  if (hour < 12) return "London session";
  if (hour < 16) return "London · New York overlap";
  if (hour < 21) return "New York session";
  return "Sydney session";
}

function freshnessLimit(timeframe: Timeframe): number {
  if (timeframe === "daily") return 3 * TIMEFRAME_MS.daily;
  return 2 * TIMEFRAME_MS[timeframe];
}

export function marketStatus(
  symbol: Symbol,
  timeframe: Timeframe,
  latestTimestamp: string | null | undefined,
  at = new Date(),
): MarketStatus {
  const open = isMarketOpen(symbol, at);
  const latestAt = latestTimestamp ? new Date(latestTimestamp) : null;
  const session = marketSession(symbol, at);

  if (!latestAt || Number.isNaN(latestAt.getTime())) {
    return {
      state: "missing",
      marketOpen: open,
      label: "No market data",
      session,
      detail: "The candle feed has not delivered a valid bar.",
      latestAt: null,
    };
  }

  if (!open) {
    return {
      state: "closed",
      marketOpen: false,
      label: "Market closed",
      session,
      detail: `Last bar ${formatUtc(latestAt)} · reopens Sunday 22:00 UTC`,
      latestAt,
    };
  }

  const ageMs = Math.max(0, at.getTime() - latestAt.getTime());
  const delayed = ageMs > freshnessLimit(timeframe);
  return {
    state: delayed ? "delayed" : "live",
    marketOpen: true,
    label: delayed ? "Data delayed" : "Market data current",
    session,
    detail: delayed
      ? `Last bar ${formatAge(ageMs)} ago · strategy execution is blocked`
      : `Last bar ${formatAge(ageMs)} ago · ${timeframe} bars`,
    latestAt,
  };
}

export function findLargestCandleGap(candles: Candle[], timeframe: Timeframe, symbol: Symbol): CandleGap | null {
  if (candles.length < 2) return null;
  const times = candles
    .map((c) => Date.parse(c.timestamp))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  if (times.length < 2) return null;

  // FX and metals have a legitimate ~49-hour weekend gap. Anything materially
  // larger than that is an ingestion/history gap and must be visible to users.
  const normalGapMs = WEEKEND_MARKETS.has(symbol)
    ? Math.max(54 * 60 * 60_000, TIMEFRAME_MS[timeframe] * 3)
    : TIMEFRAME_MS[timeframe] * 3;
  let largest: CandleGap | null = null;

  for (let i = 1; i < times.length; i += 1) {
    const durationMs = times[i] - times[i - 1];
    if (durationMs <= normalGapMs || (largest && durationMs <= largest.durationMs)) continue;
    largest = { start: new Date(times[i - 1]), end: new Date(times[i]), durationMs };
  }
  return largest;
}

export function formatDuration(ms: number): string {
  const hours = Math.round(ms / (60 * 60_000));
  if (hours >= 48) return `${Math.round(hours / 24)}d`;
  if (hours >= 1) return `${hours}h`;
  return `${Math.max(1, Math.round(ms / 60_000))}m`;
}

export function formatUtc(value: Date): string {
  return value.toLocaleString("en-GB", {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

function formatAge(ms: number): string {
  if (ms < 60_000) return "less than 1m";
  if (ms < 60 * 60_000) return `${Math.floor(ms / 60_000)}m`;
  if (ms < 48 * 60 * 60_000) return `${Math.floor(ms / (60 * 60_000))}h`;
  return `${Math.floor(ms / (24 * 60 * 60_000))}d`;
}
