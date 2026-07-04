import { prisma } from "../lib/prisma";

const DAILY_LOSS_LIMIT_PCT = 3;
const MAX_DRAWDOWN_PCT = 10;
const MIN_RR = 2;
// Tolerance so an exact-target ratio (e.g. 0.0100/0.0050) isn't rejected by
// floating-point rounding to 1.9999999998.
const RR_EPSILON = 1e-9;
const NEWS_DEFAULT_BEFORE_MIN = 30;
const NEWS_DEFAULT_AFTER_MIN = 30;

export type Impact = "LOW" | "MEDIUM" | "HIGH";

export interface NewsLite {
  title: string;
  impact: Impact;
  scheduledAt: Date | string;
}

export type Allowed = { allowed: true } | { allowed: false; reason: string };

export interface PositionSize {
  lotSize: number;
  riskAmount: number;
}

export interface RiskRewardResult {
  rr: number;
  acceptable: boolean;
}

export interface NewsWindowResult {
  safe: boolean;
  nearestEvent: string | null;
}

function log(event: string, payload: Record<string, unknown>): void {
  console.log(`[risk] ${event} ${JSON.stringify(payload)}`);
}

export function calculatePositionSize(
  accountBalance: number,
  riskPercent: number,
  entryPrice: number,
  stopLoss: number,
): PositionSize {
  if (!Number.isFinite(accountBalance) || accountBalance <= 0) {
    throw new Error("accountBalance must be a positive number");
  }
  if (!Number.isFinite(riskPercent) || riskPercent <= 0) {
    throw new Error("riskPercent must be a positive number");
  }
  if (!Number.isFinite(entryPrice) || !Number.isFinite(stopLoss)) {
    throw new Error("entryPrice and stopLoss must be finite numbers");
  }
  const distance = Math.abs(entryPrice - stopLoss);
  if (distance <= 0) {
    throw new Error("entryPrice and stopLoss must differ");
  }
  const riskAmount = accountBalance * (riskPercent / 100);
  const lotSize = riskAmount / distance;
  log("calculatePositionSize", {
    accountBalance,
    riskPercent,
    entryPrice,
    stopLoss,
    riskAmount,
    lotSize,
  });
  return { lotSize, riskAmount };
}

export function checkDailyLoss(
  userId: string,
  todayLoss: number,
  accountBalance: number,
  limitPercent: number = DAILY_LOSS_LIMIT_PCT,
): Allowed {
  const limit = accountBalance * (limitPercent / 100);
  const tripped = todayLoss > limit;
  const result: Allowed = tripped
    ? { allowed: false, reason: "Daily loss limit reached" }
    : { allowed: true };
  log("checkDailyLoss", { userId, todayLoss, accountBalance, limit, tripped });
  return result;
}

export function checkMaxDrawdown(
  peakBalance: number,
  currentBalance: number,
  limitPercent: number = MAX_DRAWDOWN_PCT,
): Allowed {
  if (!Number.isFinite(peakBalance) || peakBalance <= 0) {
    return { allowed: false, reason: "Invalid peak balance" };
  }
  const drawdownPct = ((peakBalance - currentBalance) / peakBalance) * 100;
  const tripped = drawdownPct > limitPercent;
  const result: Allowed = tripped
    ? { allowed: false, reason: "Max drawdown exceeded" }
    : { allowed: true };
  log("checkMaxDrawdown", {
    peakBalance,
    currentBalance,
    drawdownPct,
    limitPercent,
    tripped,
  });
  return result;
}

export function validateRiskReward(
  entry: number,
  stopLoss: number,
  takeProfit: number,
  minRR: number = MIN_RR,
): RiskRewardResult {
  const risk = Math.abs(entry - stopLoss);
  const reward = Math.abs(takeProfit - entry);
  if (risk <= 0) {
    log("validateRiskReward", { entry, stopLoss, takeProfit, error: "risk_is_zero" });
    return { rr: 0, acceptable: false };
  }
  const rr = reward / risk;
  const result: RiskRewardResult = { rr, acceptable: rr >= minRR - RR_EPSILON };
  log("validateRiskReward", { entry, stopLoss, takeProfit, rr, acceptable: result.acceptable });
  return result;
}

export function isNewsWindow(
  upcomingNews: NewsLite[],
  minutesBefore: number = NEWS_DEFAULT_BEFORE_MIN,
  minutesAfter: number = NEWS_DEFAULT_AFTER_MIN,
  now: Date = new Date(),
): NewsWindowResult {
  const high = upcomingNews.filter((n) => n.impact === "HIGH");
  let nearestEvent: string | null = null;
  let nearestAbsMin = Number.POSITIVE_INFINITY;
  let inWindow = false;

  for (const event of high) {
    const at = event.scheduledAt instanceof Date ? event.scheduledAt : new Date(event.scheduledAt);
    if (Number.isNaN(at.getTime())) continue;
    const deltaMin = (at.getTime() - now.getTime()) / 60_000;
    const absMin = Math.abs(deltaMin);
    if (absMin < nearestAbsMin) {
      nearestAbsMin = absMin;
      nearestEvent = event.title;
    }
    if (deltaMin >= -minutesAfter && deltaMin <= minutesBefore) {
      inWindow = true;
    }
  }

  const result: NewsWindowResult = { safe: !inWindow, nearestEvent };
  log("isNewsWindow", {
    highImpactCount: high.length,
    minutesBefore,
    minutesAfter,
    nearestEvent,
    nearestAbsMin: Number.isFinite(nearestAbsMin) ? nearestAbsMin : null,
    safe: result.safe,
  });
  return result;
}

/** Runtime-resolved thresholds. Any omitted field falls back to the legacy
 *  constant, so callers that don't pass config behave exactly as before. */
export interface RiskThresholds {
  minRR?: number;
  dailyLossLimitPct?: number;
  maxDrawdownPct?: number;
  newsBeforeMin?: number;
  newsAfterMin?: number;
}

export interface ValidateTradeInput {
  userId: string;
  symbol: string;
  entry: number;
  stopLoss: number;
  takeProfit: number;
  accountBalance: number;
  peakBalance: number;
  todayLoss: number;
  riskPercent: number;
  upcomingNews: NewsLite[];
  thresholds?: RiskThresholds;
  /** Optional: Gold multi-strategy context for enhanced risk checks. */
  goldContext?: GoldRiskContext;
}

export interface ValidateTradeResult {
  approved: boolean;
  positionSize: number;
  reasons: string[];
}

export async function validateTrade(input: ValidateTradeInput): Promise<ValidateTradeResult> {
  const reasons: string[] = [];
  const t = input.thresholds ?? {};
  const minRR = t.minRR ?? MIN_RR;
  const dailyLossLimitPct = t.dailyLossLimitPct ?? DAILY_LOSS_LIMIT_PCT;
  const maxDrawdownPct = t.maxDrawdownPct ?? MAX_DRAWDOWN_PCT;
  const newsBeforeMin = t.newsBeforeMin ?? NEWS_DEFAULT_BEFORE_MIN;
  const newsAfterMin = t.newsAfterMin ?? NEWS_DEFAULT_AFTER_MIN;

  let positionSize = 0;
  try {
    const sized = calculatePositionSize(
      input.accountBalance,
      input.riskPercent,
      input.entry,
      input.stopLoss,
    );
    positionSize = sized.lotSize;
  } catch (err) {
    reasons.push(err instanceof Error ? err.message : "Invalid position size inputs");
  }

  const daily = checkDailyLoss(input.userId, input.todayLoss, input.accountBalance, dailyLossLimitPct);
  if (!daily.allowed) reasons.push(daily.reason);

  const dd = checkMaxDrawdown(input.peakBalance, input.accountBalance, maxDrawdownPct);
  if (!dd.allowed) reasons.push(dd.reason);

  const rr = validateRiskReward(input.entry, input.stopLoss, input.takeProfit, minRR);
  if (!rr.acceptable) {
    reasons.push(`Risk/reward ${rr.rr.toFixed(2)} below minimum ${minRR}`);
  }

  const news = isNewsWindow(input.upcomingNews, newsBeforeMin, newsAfterMin);
  if (!news.safe) {
    reasons.push(
      news.nearestEvent
        ? `Inside news window: ${news.nearestEvent}`
        : "Inside high-impact news window",
    );
  }

  // --- Gold multi-strategy risk checks ---
  if (input.goldContext) {
    const goldReasons = validateGoldRisk(input.goldContext);
    reasons.push(...goldReasons);
  }

  const approved = reasons.length === 0;
  const dailyLossLimit = input.accountBalance * (dailyLossLimitPct / 100);

  try {
    await prisma.riskLog.create({
      data: {
        accountBalance: input.accountBalance.toFixed(2),
        riskPercent: input.riskPercent.toFixed(4),
        positionSize: positionSize.toFixed(8),
        dailyLoss: input.todayLoss.toFixed(2),
        dailyLossLimit: dailyLossLimit.toFixed(2),
        circuitBreakerTripped: !approved,
      },
    });
  } catch (err) {
    console.error("[risk] failed to persist RiskLog:", err);
  }

  log("validateTrade", {
    userId: input.userId,
    symbol: input.symbol,
    approved,
    positionSize,
    reasons,
  });

  return { approved, positionSize, reasons };
}

// =========================================================================
// Gold Multi-Strategy Risk Management
// =========================================================================

/** Max concurrent Gold positions (across all 4 strategies). */
const GOLD_MAX_CONCURRENT = 3;
/** Max positions in the same direction (prevent directional stacking). */
const GOLD_MAX_SAME_DIRECTION = 2;
/** Daily profit target — once hit, reduce risk per trade. */
const GOLD_DAILY_TARGET_PCT = 1.5;
/** Reduced risk per trade after hitting daily target. */
const GOLD_REDUCED_RISK_PCT = 0.5;
/** Max consecutive losses in a session before pausing. */
const GOLD_MAX_CONSECUTIVE_LOSSES = 3;
/** Max risk allocated to a single session (Asian/London/NY). */
const GOLD_SESSION_RISK_BUDGET_PCT = 1.0;

export interface OpenGoldPosition {
  symbol: string;
  direction: "LONG" | "SHORT";
  strategy: string;
  session: string;
  riskAmount: number;
}

export interface GoldRiskContext {
  /** Currently open Gold positions. */
  openPositions: OpenGoldPosition[];
  /** Direction of the new trade being evaluated. */
  direction: "LONG" | "SHORT";
  /** Strategy requesting the trade. */
  strategyName: string;
  /** Current session label (ASIAN/LONDON/NEWYORK). */
  session: string;
  /** Today's realized P&L as a % of balance (positive = profit). */
  todayPnlPct: number;
  /** Consecutive losses in the current session. */
  sessionConsecutiveLosses: number;
  /** Total risk already allocated to the current session (as $ amount). */
  sessionRiskUsed: number;
  /** Account balance for session budget calculation. */
  accountBalance: number;
}

/**
 * Validate Gold-specific multi-strategy risk constraints.
 *
 * These checks run IN ADDITION TO the base risk engine checks (daily loss,
 * drawdown, RR, news window). They prevent the 4-strategy orchestra from
 * over-exposing the account to Gold.
 */
export function validateGoldRisk(ctx: GoldRiskContext): string[] {
  const reasons: string[] = [];

  // 1. Max concurrent Gold positions.
  if (ctx.openPositions.length >= GOLD_MAX_CONCURRENT) {
    reasons.push(
      `Gold concurrent limit: ${ctx.openPositions.length}/${GOLD_MAX_CONCURRENT} positions already open`,
    );
  }

  // 2. Directional correlation guard — prevent stacking same direction.
  const sameDir = ctx.openPositions.filter((p) => p.direction === ctx.direction);
  if (sameDir.length >= GOLD_MAX_SAME_DIRECTION) {
    reasons.push(
      `Gold direction limit: ${sameDir.length}/${GOLD_MAX_SAME_DIRECTION} ${ctx.direction} positions already open`,
    );
  }

  // 3. Consecutive loss circuit breaker — pause until next session.
  if (ctx.sessionConsecutiveLosses >= GOLD_MAX_CONSECUTIVE_LOSSES) {
    reasons.push(
      `Gold session paused: ${ctx.sessionConsecutiveLosses} consecutive losses in ${ctx.session} session`,
    );
  }

  // 4. Session risk budget — max total risk per session.
  const sessionBudget = ctx.accountBalance * (GOLD_SESSION_RISK_BUDGET_PCT / 100);
  if (ctx.sessionRiskUsed >= sessionBudget) {
    reasons.push(
      `Gold session budget exhausted: $${ctx.sessionRiskUsed.toFixed(2)} / $${sessionBudget.toFixed(2)} in ${ctx.session}`,
    );
  }

  // 5. Trailing daily target — if we've hit 1.5% profit today, log a warning.
  //    The caller should reduce riskPercent, but we don't block the trade.
  if (ctx.todayPnlPct >= GOLD_DAILY_TARGET_PCT) {
    log("goldRisk_dailyTargetHit", {
      todayPnlPct: ctx.todayPnlPct,
      threshold: GOLD_DAILY_TARGET_PCT,
      recommendation: `Reduce risk to ${GOLD_REDUCED_RISK_PCT}% per trade`,
    });
  }

  if (reasons.length > 0) {
    log("goldRisk_blocked", {
      strategy: ctx.strategyName,
      direction: ctx.direction,
      session: ctx.session,
      reasons,
    });
  }

  return reasons;
}

/**
 * Get the adjusted risk percentage for a Gold trade.
 *
 * If the daily target has been hit, returns a reduced risk percentage.
 * Otherwise returns the base risk percentage unchanged.
 */
export function getGoldAdjustedRisk(
  baseRiskPct: number,
  todayPnlPct: number,
): number {
  if (todayPnlPct >= GOLD_DAILY_TARGET_PCT) {
    log("goldRisk_reducedRisk", {
      baseRiskPct,
      adjustedRiskPct: GOLD_REDUCED_RISK_PCT,
      reason: `Daily target ${GOLD_DAILY_TARGET_PCT}% hit (current: ${todayPnlPct.toFixed(2)}%)`,
    });
    return GOLD_REDUCED_RISK_PCT;
  }
  return baseRiskPct;
}

