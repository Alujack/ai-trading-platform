/**
 * Symbol mapping + units→lots conversion (Plan 08, Phases 2–3 seed).
 *
 * Exness symbol names are mostly unsuffixed (EURUSD, XAUUSD, BTCUSD) but vary by
 * account type — ALWAYS verify against the live MT5 Market Watch. Override the
 * map without a code change via BROKER_SYMBOL_MAP (JSON), e.g.
 *   BROKER_SYMBOL_MAP={"EURUSD":"EURUSDm","BTCUSD":"BTCUSDm"}
 */
import type { SymbolSpec } from "./types";

const DEFAULT_MAP: Record<string, string> = {
  EURUSD: "EURUSD",
  XAUUSD: "XAUUSD",
  BTCUSD: "BTCUSD",
};

let cached: Record<string, string> | null = null;

function loadMap(): Record<string, string> {
  if (cached) return cached;
  const raw = process.env.BROKER_SYMBOL_MAP;
  if (!raw || !raw.trim()) {
    cached = DEFAULT_MAP;
    return cached;
  }
  try {
    const override = JSON.parse(raw) as Record<string, string>;
    cached = { ...DEFAULT_MAP, ...override };
  } catch {
    console.error(`[broker] invalid BROKER_SYMBOL_MAP JSON — using defaults`);
    cached = DEFAULT_MAP;
  }
  return cached;
}

/** Map an internal symbol (EURUSD) to the broker-native name. Identity if unmapped. */
export function brokerSymbol(internal: string): string {
  return loadMap()[internal] ?? internal;
}

/** Test-only: clear the memoized BROKER_SYMBOL_MAP. */
export function __resetSymbolMap(): void {
  cached = null;
}

/**
 * Convert a raw unit size (risk engine output) into a broker-valid lot size:
 * divide by contract size, floor to the volume step, clamp to [min, max].
 * Returns 0 when the result is below the broker's minimum (caller must reject).
 */
export function lotsFromUnits(units: number, spec: SymbolSpec): number {
  if (!Number.isFinite(units) || units <= 0) return 0;
  if (!(spec.contractSize > 0) || !(spec.volumeStep > 0)) {
    throw new Error("symbol spec must have positive contractSize and volumeStep");
  }
  const rawLots = units / spec.contractSize;
  const steps = Math.floor(rawLots / spec.volumeStep + 1e-9);
  let lots = Number((steps * spec.volumeStep).toFixed(8));
  if (lots < spec.volumeMin) return 0;
  if (lots > spec.volumeMax) lots = spec.volumeMax;
  return lots;
}
