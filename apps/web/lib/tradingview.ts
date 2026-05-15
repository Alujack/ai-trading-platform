import type { Symbol, Timeframe } from "./types";

export const TV_SYMBOL: Record<Symbol, string> = {
  XAUUSD: "OANDA:XAUUSD",
  EURUSD: "FX:EURUSD",
  BTCUSD: "BITSTAMP:BTCUSD",
};

export const TV_INTERVAL: Record<Timeframe, string> = {
  "1min": "1",
  "5min": "5",
  "15min": "15",
  "60min": "60",
  daily: "D",
};
