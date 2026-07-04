/**
 * Trailing Stop Manager — ATR-based dynamic trailing stops for Gold trades.
 *
 * This module provides three trade management features:
 *
 * 1. **Breakeven Lock** — Move SL to entry + spread after 0.5×ATR profit.
 *    Converts the trade into a risk-free position.
 *
 * 2. **ATR Trailing Stop** — After price moves 1×ATR in profit, trail the stop
 *    by a configurable ATR multiple (default 1.5×ATR behind price).
 *
 * 3. **Time-based Exit** — Close any trade open > maxHoldMinutes to avoid
 *    holding through low-volume dead zones.
 *
 * 4. **Partial Profit Taking** — Scale out 50% at TP1 (1×ATR), trail rest.
 *
 * Usage:
 *   import { evaluateTrailingStop, TrailingStopConfig } from "./trailingStopManager";
 *
 *   const action = evaluateTrailingStop({
 *     entryPrice: 2650.00,
 *     currentPrice: 2662.50,
 *     currentStop: 2640.00,
 *     direction: "LONG",
 *     atr: 12.5,
 *     openedAt: new Date("2026-07-04T14:00:00Z"),
 *     now: new Date(),
 *   });
 *   // action = { type: "TRAIL", newStop: 2648.75, reason: "..." }
 */

// --- Configuration ---

export interface TrailingStopConfig {
  /** Move SL to breakeven after this × ATR profit (default 0.5). */
  breakevenTriggerAtr: number;
  /** Spread to add beyond entry for breakeven (in price units, default 0.20 for Gold). */
  breakevenSpread: number;
  /** Start trailing after this × ATR profit (default 1.0). */
  trailTriggerAtr: number;
  /** Trail distance: keep SL this × ATR behind price (default 1.5). */
  trailDistanceAtr: number;
  /** Max time to hold a trade in minutes (default 240 = 4 hours). */
  maxHoldMinutes: number;
  /** Partial take-profit at this × ATR (default 1.0). Close 50%. */
  partialTpAtr: number;
  /** Fraction of position to close at partial TP (default 0.5 = 50%). */
  partialTpFraction: number;
}

export const DEFAULT_TRAILING_CONFIG: TrailingStopConfig = {
  breakevenTriggerAtr: 0.5,
  breakevenSpread: 0.20,
  trailTriggerAtr: 1.0,
  trailDistanceAtr: 1.5,
  maxHoldMinutes: 240,
  partialTpAtr: 1.0,
  partialTpFraction: 0.5,
};

// --- Input/Output types ---

export interface TrailingStopInput {
  entryPrice: number;
  currentPrice: number;
  currentStop: number;
  direction: "LONG" | "SHORT";
  atr: number;
  openedAt: Date;
  now?: Date;
  config?: Partial<TrailingStopConfig>;
  /** Whether partial TP has already been taken. */
  partialTaken?: boolean;
  /** Current position size (lots). */
  positionSize?: number;
}

export type TrailingStopAction =
  | { type: "HOLD"; reason: string }
  | { type: "TRAIL"; newStop: number; reason: string }
  | { type: "BREAKEVEN"; newStop: number; reason: string }
  | { type: "PARTIAL_CLOSE"; closeSize: number; newStop: number; reason: string }
  | { type: "TIME_EXIT"; reason: string };

// --- Core logic ---

function log(event: string, payload: Record<string, unknown>): void {
  console.log(`[trailing] ${event} ${JSON.stringify(payload)}`);
}

export function evaluateTrailingStop(input: TrailingStopInput): TrailingStopAction {
  const cfg: TrailingStopConfig = { ...DEFAULT_TRAILING_CONFIG, ...input.config };
  const now = input.now ?? new Date();
  const {
    entryPrice,
    currentPrice,
    currentStop,
    direction,
    atr,
    openedAt,
  } = input;

  // 1. Time-based exit check.
  const holdMinutes = (now.getTime() - openedAt.getTime()) / 60_000;
  if (holdMinutes >= cfg.maxHoldMinutes) {
    log("time_exit", { holdMinutes, maxHoldMinutes: cfg.maxHoldMinutes });
    return {
      type: "TIME_EXIT",
      reason: `Trade open ${Math.round(holdMinutes)}min exceeds ${cfg.maxHoldMinutes}min limit`,
    };
  }

  // Calculate unrealized profit in ATR units.
  const unrealizedPips =
    direction === "LONG" ? currentPrice - entryPrice : entryPrice - currentPrice;
  const profitAtr = atr > 0 ? unrealizedPips / atr : 0;

  // 2. Partial profit taking (if not already taken and position size known).
  if (
    !input.partialTaken &&
    input.positionSize &&
    input.positionSize > 0 &&
    profitAtr >= cfg.partialTpAtr
  ) {
    const closeSize = Math.round(input.positionSize * cfg.partialTpFraction * 100) / 100;
    // Also move stop to breakeven when taking partial.
    const beStop =
      direction === "LONG"
        ? entryPrice + cfg.breakevenSpread
        : entryPrice - cfg.breakevenSpread;

    log("partial_close", {
      profitAtr: profitAtr.toFixed(2),
      closeSize,
      newStop: beStop,
    });
    return {
      type: "PARTIAL_CLOSE",
      closeSize,
      newStop: beStop,
      reason: `Partial TP: ${profitAtr.toFixed(1)}×ATR profit, closing ${(cfg.partialTpFraction * 100).toFixed(0)}% (${closeSize} lots), SL → breakeven`,
    };
  }

  // 3. ATR trailing stop (higher priority than breakeven if we're far enough).
  if (profitAtr >= cfg.trailTriggerAtr) {
    const trailStop =
      direction === "LONG"
        ? currentPrice - cfg.trailDistanceAtr * atr
        : currentPrice + cfg.trailDistanceAtr * atr;

    // Only move the stop if the new level is BETTER (closer to current price)
    // than the existing stop. Never widen a stop.
    const isBetter =
      direction === "LONG" ? trailStop > currentStop : trailStop < currentStop;

    if (isBetter) {
      log("trail", {
        profitAtr: profitAtr.toFixed(2),
        currentStop,
        newStop: trailStop,
        trailDistanceAtr: cfg.trailDistanceAtr,
      });
      return {
        type: "TRAIL",
        newStop: Math.round(trailStop * 100) / 100,
        reason: `Trailing: ${profitAtr.toFixed(1)}×ATR profit, SL → ${trailStop.toFixed(2)} (${cfg.trailDistanceAtr}×ATR behind price)`,
      };
    }
  }

  // 4. Breakeven lock.
  if (profitAtr >= cfg.breakevenTriggerAtr) {
    const beStop =
      direction === "LONG"
        ? entryPrice + cfg.breakevenSpread
        : entryPrice - cfg.breakevenSpread;

    // Only move to breakeven if current stop is still below entry (for longs).
    const notYetBe =
      direction === "LONG" ? currentStop < entryPrice : currentStop > entryPrice;

    if (notYetBe) {
      log("breakeven", {
        profitAtr: profitAtr.toFixed(2),
        currentStop,
        newStop: beStop,
      });
      return {
        type: "BREAKEVEN",
        newStop: Math.round(beStop * 100) / 100,
        reason: `Breakeven lock: ${profitAtr.toFixed(1)}×ATR profit, SL → ${beStop.toFixed(2)} (entry + spread)`,
      };
    }
  }

  // 5. No action needed.
  return {
    type: "HOLD",
    reason: `Profit ${profitAtr.toFixed(1)}×ATR — below trigger thresholds`,
  };
}
