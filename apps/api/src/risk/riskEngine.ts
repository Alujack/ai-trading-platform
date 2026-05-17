import { prisma } from "../lib/prisma";

const DAILY_LOSS_LIMIT_PCT = 3;
const MAX_DRAWDOWN_PCT = 10;
const MIN_RR = 2;
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
  const result: RiskRewardResult = { rr, acceptable: rr >= minRR };
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
}

export interface ValidateTradeResult {
  approved: boolean;
  positionSize: number;
  reasons: string[];
}

export async function validateTrade(input: ValidateTradeInput): Promise<ValidateTradeResult> {
  const reasons: string[] = [];

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

  const daily = checkDailyLoss(input.userId, input.todayLoss, input.accountBalance);
  if (!daily.allowed) reasons.push(daily.reason);

  const dd = checkMaxDrawdown(input.peakBalance, input.accountBalance);
  if (!dd.allowed) reasons.push(dd.reason);

  const rr = validateRiskReward(input.entry, input.stopLoss, input.takeProfit);
  if (!rr.acceptable) {
    reasons.push(`Risk/reward ${rr.rr.toFixed(2)} below minimum ${MIN_RR}`);
  }

  const news = isNewsWindow(input.upcomingNews);
  if (!news.safe) {
    reasons.push(
      news.nearestEvent
        ? `Inside news window: ${news.nearestEvent}`
        : "Inside high-impact news window",
    );
  }

  const approved = reasons.length === 0;
  const dailyLossLimit = input.accountBalance * (DAILY_LOSS_LIMIT_PCT / 100);

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
