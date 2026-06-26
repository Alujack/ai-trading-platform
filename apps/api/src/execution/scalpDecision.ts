/**
 * Pure decision core for the fast scalp manager (Phase 3). No I/O — so it is
 * unit-tested in isolation (no prisma/broker import chain), the same way the risk
 * engine and paperTrading's `evaluateExit` keep their decision logic pure.
 *
 * Everything is in R = unrealized profit ÷ the trade's risk amount, so thresholds
 * are scale-invariant across symbols and account sizes. See scalpManager.ts for the
 * loop that feeds this and acts on its decisions, and for the rule rationale.
 */

export interface ScalpManageConfig {
  /** First-check close if actualStopDist / intendedStopDist < this (fill slipped toward SL). */
  minStopRatio: number;
  /** One-check emergency close at R <= -emergencyR. */
  emergencyR: number;
  /** Two-check adverse engages once R <= -watchR. */
  watchR: number;
  /** Profit-lock arms once best-seen R >= trailStartR. */
  trailStartR: number;
  /** Once armed, close if R drops to (bestR - trailGivebackR). */
  trailGivebackR: number;
}

function envNum(name: string, fallback: number): number {
  const v = Number(process.env[name]);
  return Number.isFinite(v) ? v : fallback;
}

export function loadConfig(): ScalpManageConfig {
  return {
    minStopRatio: envNum("SCALP_MIN_STOP_RATIO", 0.5),
    emergencyR: envNum("SCALP_EMERGENCY_R", 0.8),
    watchR: envNum("SCALP_WATCH_R", 0.5),
    trailStartR: envNum("SCALP_TRAIL_START_R", 1.0),
    trailGivebackR: envNum("SCALP_TRAIL_GIVEBACK_R", 0.5),
  };
}

export interface TicketState {
  checks: number;
  lastR: number;
  bestR: number;
}

export interface ScalpDecisionInput {
  /** Prior state for this ticket, or undefined on first sight. */
  state: TicketState | undefined;
  /** Current unrealized profit ÷ risk amount. */
  r: number;
  /** |signal.entry − signal.stop| — the stop distance we intended. */
  intendedStopDist: number;
  /** |position.openPrice − position.stopLoss| — the stop distance after the real fill. */
  actualStopDist: number;
}

export interface ScalpDecision {
  action: "close" | "hold";
  reason: string;
  nextState: TicketState;
}

/**
 * Given the prior state and the current R / stop distances, return whether to close
 * (and why) plus the updated state. Precedence: unsafe-stop (first sight only) →
 * single-check emergency → two-check adverse → profit give-back lock → hold.
 */
export function decideScalpAction(input: ScalpDecisionInput, cfg: ScalpManageConfig): ScalpDecision {
  const { state, r, intendedStopDist, actualStopDist } = input;
  const firstCheck = state === undefined;
  const worsened = state !== undefined && r < state.lastR;
  const bestR = state === undefined ? r : Math.max(state.bestR, r);
  const nextState: TicketState = {
    checks: (state?.checks ?? 0) + 1,
    lastR: r,
    bestR,
  };

  // 1. Slippage-jammed stop — only meaningful on first sight (we never modify the SL).
  if (firstCheck && intendedStopDist > 0 && actualStopDist / intendedStopDist < cfg.minStopRatio) {
    return { action: "close", reason: "unsafe_stop_slippage", nextState };
  }

  // 2. Single-check emergency.
  if (r <= -cfg.emergencyR) {
    return { action: "close", reason: "emergency_adverse", nextState };
  }

  // 3. Two-check adverse: worse than last check while already in adverse territory.
  if (worsened && r <= -cfg.watchR) {
    return { action: "close", reason: "two_check_adverse", nextState };
  }

  // 4. Profit give-back lock.
  if (bestR >= cfg.trailStartR && r <= bestR - cfg.trailGivebackR) {
    return { action: "close", reason: "profit_lock", nextState };
  }

  return { action: "hold", reason: "hold", nextState };
}
