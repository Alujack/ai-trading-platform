import { Prisma } from "@prisma/client";
import { prisma } from "../lib/prisma";
import { validateTrade, type Impact, type NewsLite } from "../risk/riskEngine";

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";

const STRATEGY_RSI_MIN = 40;
const STRATEGY_RSI_MAX = 55;
const STRATEGY_ATR_MIN = 5;
const ATR_STOP_MULT = 1.5;
const ATR_TARGET_MULT = 3;
const AI_MIN_SCORE = 70;
const COOLDOWN_MS = 60 * 60 * 1000;
const CANDLE_LOOKBACK = 50;
const INDICATOR_LOOKBACK = 10;

export type GenerationStatus = "skipped" | "rejected" | "generated";

export interface GenerationResult {
  status: GenerationStatus;
  reason?: string;
  signalId?: string;
  score?: number;
}

interface AiValidateResponse {
  score: number;
  approved: boolean;
  reasoning: string;
  concerns: string[];
}

function decToNum(d: Prisma.Decimal | null | undefined): number {
  if (d == null) return Number.NaN;
  return Number(d.toString());
}

function readAccountState() {
  return {
    userId: process.env.PAPER_USER_ID ?? "system",
    accountBalance: Number(process.env.PAPER_ACCOUNT_BALANCE ?? "10000"),
    peakBalance: Number(process.env.PAPER_PEAK_BALANCE ?? "10000"),
    riskPercent: Number(process.env.PAPER_RISK_PERCENT ?? "1"),
  };
}

async function computeTodayLoss(): Promise<number> {
  const startOfDay = new Date();
  startOfDay.setUTCHours(0, 0, 0, 0);
  const trades = await prisma.trade.findMany({
    where: { closedAt: { gte: startOfDay }, status: "CLOSED" },
    select: { profitLoss: true },
  });
  return trades.reduce((sum, t) => {
    const pl = t.profitLoss ? Number(t.profitLoss.toString()) : 0;
    return pl < 0 ? sum + Math.abs(pl) : sum;
  }, 0);
}

export async function generateSignal(
  symbol: string,
  timeframe: string,
): Promise<GenerationResult> {
  const recent = await prisma.signal.findFirst({
    where: { symbol, timeframe, status: { in: ["PENDING", "ACTIVE"] } },
    orderBy: { createdAt: "desc" },
  });
  if (recent && Date.now() - recent.createdAt.getTime() < COOLDOWN_MS) {
    return { status: "skipped", reason: "cooldown_active" };
  }

  const candles = await prisma.candle.findMany({
    where: { symbol, timeframe },
    orderBy: { timestamp: "desc" },
    take: CANDLE_LOOKBACK,
  });
  if (candles.length < 10) {
    return { status: "skipped", reason: `insufficient_candles=${candles.length}` };
  }

  const indicators = await prisma.indicator.findMany({
    where: {
      symbol,
      timeframe,
      timestamp: { in: candles.map((c) => c.timestamp) },
    },
    orderBy: { timestamp: "desc" },
  });
  if (indicators.length === 0) {
    return { status: "skipped", reason: "no_indicators" };
  }

  const latest = indicators[0];
  const latestCandle = candles[0];
  if (!latest || !latestCandle) {
    return { status: "skipped", reason: "missing_latest_row" };
  }

  const ema20 = decToNum(latest.ema20);
  const ema50 = decToNum(latest.ema50);
  const rsi = decToNum(latest.rsi);
  const atr = decToNum(latest.atr);

  if (
    !Number.isFinite(ema20) ||
    !Number.isFinite(ema50) ||
    !Number.isFinite(rsi) ||
    !Number.isFinite(atr)
  ) {
    return { status: "skipped", reason: "missing_indicator_values" };
  }

  if (!(ema20 > ema50)) {
    return { status: "skipped", reason: `ema_not_bullish ema20=${ema20} ema50=${ema50}` };
  }
  if (!(rsi >= STRATEGY_RSI_MIN && rsi <= STRATEGY_RSI_MAX)) {
    return { status: "skipped", reason: `rsi_out_of_range rsi=${rsi.toFixed(1)}` };
  }
  if (!(atr > STRATEGY_ATR_MIN)) {
    return { status: "skipped", reason: `atr_too_low atr=${atr.toFixed(2)}` };
  }

  const strategyChecks = [
    `EMA20 ${ema20.toFixed(4)} > EMA50 ${ema50.toFixed(4)} (bullish trend)`,
    `RSI ${rsi.toFixed(1)} in [${STRATEGY_RSI_MIN}, ${STRATEGY_RSI_MAX}] (pullback entry)`,
    `ATR ${atr.toFixed(4)} > ${STRATEGY_ATR_MIN} (sufficient volatility)`,
  ];

  const entry = decToNum(latestCandle.close);
  const stopLoss = entry - ATR_STOP_MULT * atr;
  const takeProfit = entry + ATR_TARGET_MULT * atr;

  const upcomingNews = await prisma.newsEvent.findMany({
    where: { scheduledAt: { gt: new Date() } },
    orderBy: { scheduledAt: "asc" },
    take: 5,
  });

  const aiBody = {
    signal: {
      symbol,
      timeframe,
      direction: "LONG" as const,
      entryPrice: entry,
      stopLoss,
      takeProfit,
      confidenceScore: 0,
      aiReasoning: strategyChecks.join("; "),
    },
    candles: candles.map((c) => ({
      timestamp: c.timestamp.toISOString(),
      open: decToNum(c.open),
      high: decToNum(c.high),
      low: decToNum(c.low),
      close: decToNum(c.close),
      volume: decToNum(c.volume),
    })),
    indicators: indicators.slice(0, INDICATOR_LOOKBACK).map((i) => ({
      timestamp: i.timestamp.toISOString(),
      rsi: i.rsi ? decToNum(i.rsi) : null,
      ema20: i.ema20 ? decToNum(i.ema20) : null,
      ema50: i.ema50 ? decToNum(i.ema50) : null,
      ema200: i.ema200 ? decToNum(i.ema200) : null,
      atr: i.atr ? decToNum(i.atr) : null,
    })),
    upcomingNews: upcomingNews.map((n) => ({
      title: n.title,
      impact: n.impact,
      currency: n.currency,
      scheduledAt: n.scheduledAt.toISOString(),
    })),
  };

  let aiResult: AiValidateResponse;
  try {
    const res = await fetch(`${AI_SERVICE_URL}/analyze/validate-signal`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(aiBody),
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      return {
        status: "skipped",
        reason: `ai_service_${res.status}: ${errText.slice(0, 120)}`,
      };
    }
    aiResult = (await res.json()) as AiValidateResponse;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { status: "skipped", reason: `ai_service_unreachable: ${msg}` };
  }

  if (typeof aiResult.score !== "number" || aiResult.score < AI_MIN_SCORE) {
    return {
      status: "rejected",
      reason: `ai_score_too_low score=${aiResult.score}`,
      score: aiResult.score,
    };
  }

  const newsLite: NewsLite[] = upcomingNews.map((n) => ({
    title: n.title,
    impact: n.impact as Impact,
    scheduledAt: n.scheduledAt,
  }));

  const account = readAccountState();
  const todayLoss = await computeTodayLoss();
  const risk = await validateTrade({
    userId: account.userId,
    symbol,
    entry,
    stopLoss,
    takeProfit,
    accountBalance: account.accountBalance,
    peakBalance: account.peakBalance,
    todayLoss,
    riskPercent: account.riskPercent,
    upcomingNews: newsLite,
  });

  if (!risk.approved) {
    return {
      status: "rejected",
      reason: `risk_rejected: ${risk.reasons.join("; ")}`,
      score: aiResult.score,
    };
  }

  const concernsLine =
    aiResult.concerns && aiResult.concerns.length > 0
      ? aiResult.concerns.join("; ")
      : "none";

  const reasoning = [
    "Strategy:",
    ...strategyChecks.map((c) => `  - ${c}`),
    "",
    `AI score: ${aiResult.score}`,
    `AI reasoning: ${aiResult.reasoning}`,
    `AI concerns: ${concernsLine}`,
    "",
    `Risk approved. Position size ${risk.positionSize.toFixed(8)} units.`,
  ].join("\n");

  const signal = await prisma.signal.create({
    data: {
      symbol,
      timeframe,
      direction: "LONG",
      entryPrice: entry.toFixed(8),
      stopLoss: stopLoss.toFixed(8),
      takeProfit: takeProfit.toFixed(8),
      confidenceScore: Math.round(aiResult.score),
      aiReasoning: reasoning,
      status: "PENDING",
    },
  });

  return { status: "generated", signalId: signal.id, score: aiResult.score };
}
