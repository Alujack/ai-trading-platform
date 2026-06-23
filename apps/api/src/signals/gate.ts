import { Prisma } from "@prisma/client";
import { prisma } from "../lib/prisma";
import { validateTrade, type Impact, type NewsLite } from "../risk/riskEngine";

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";
const DEFAULT_AI_MIN_SCORE = 70;
const CANDLE_LOOKBACK = 50;
const INDICATOR_LOOKBACK = 10;
const NEWS_LOOKAHEAD = 5;

/**
 * A strategy's proposed trade, before it has been validated. Every strategy —
 * Python or TS — funnels through `gateCandidate()`, which is the single place
 * that calls the AI validator and the risk engine (per the CLAUDE.md rule
 * "Risk engine must be called before any trade execution").
 */
export interface SignalCandidate {
  strategyName: string;
  symbol: string;
  timeframe: string;
  direction: "LONG" | "SHORT";
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  /** Strategy's own pre-AI confidence (0–100); informational, not the gate. */
  confidence: number;
  /** Human-readable strategy rationale, folded into the stored aiReasoning. */
  reasoning: string;
  /** Deterministic id for idempotency (e.g. a per-bar hash). Optional. */
  clientId?: string;
  /** If set (>0), reject a new candidate while an open signal for the same
   *  (symbol, timeframe, strategy) is younger than this many ms. */
  cooldownMs?: number;
  /** AI score floor; defaults to 70. */
  aiMinScore?: number;
}

export type GateStatus = "skipped" | "rejected" | "generated";

export interface GateResult {
  status: GateStatus;
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

/**
 * Validate a candidate through AI + risk, and persist a PENDING Signal tagged
 * with its strategy when both gates pass. This is the one gate for all
 * strategies; the per-strategy detection logic lives in the strategy modules.
 */
export async function gateCandidate(candidate: SignalCandidate): Promise<GateResult> {
  const { strategyName, symbol, timeframe, direction } = candidate;

  // Idempotency: a candidate carrying a clientId is the same trade if we've
  // already stored it (lets per-bar strategies re-emit safely).
  if (candidate.clientId) {
    const existing = await prisma.signal.findUnique({ where: { id: candidate.clientId } });
    if (existing) return { status: "skipped", reason: "idempotent_duplicate" };
  }

  // Cooldown: suppress a fresh signal while one is still open for this strategy.
  if (candidate.cooldownMs && candidate.cooldownMs > 0) {
    const recent = await prisma.signal.findFirst({
      where: { symbol, timeframe, strategyName, status: { in: ["PENDING", "ACTIVE"] } },
      orderBy: { createdAt: "desc" },
    });
    if (recent && Date.now() - recent.createdAt.getTime() < candidate.cooldownMs) {
      return { status: "skipped", reason: "cooldown_active" };
    }
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
    where: { symbol, timeframe, timestamp: { in: candles.map((c) => c.timestamp) } },
    orderBy: { timestamp: "desc" },
  });

  const upcomingNews = await prisma.newsEvent.findMany({
    where: { scheduledAt: { gt: new Date() } },
    orderBy: { scheduledAt: "asc" },
    take: NEWS_LOOKAHEAD,
  });

  const aiBody = {
    signal: {
      symbol,
      timeframe,
      direction,
      entryPrice: candidate.entryPrice,
      stopLoss: candidate.stopLoss,
      takeProfit: candidate.takeProfit,
      confidenceScore: Math.round(candidate.confidence),
      aiReasoning: candidate.reasoning,
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
      return { status: "skipped", reason: `ai_service_${res.status}: ${errText.slice(0, 120)}` };
    }
    aiResult = (await res.json()) as AiValidateResponse;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { status: "skipped", reason: `ai_service_unreachable: ${msg}` };
  }

  const minScore = candidate.aiMinScore ?? DEFAULT_AI_MIN_SCORE;
  if (typeof aiResult.score !== "number" || aiResult.score < minScore) {
    return { status: "rejected", reason: `ai_score_too_low score=${aiResult.score}`, score: aiResult.score };
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
    entry: candidate.entryPrice,
    stopLoss: candidate.stopLoss,
    takeProfit: candidate.takeProfit,
    accountBalance: account.accountBalance,
    peakBalance: account.peakBalance,
    todayLoss,
    riskPercent: account.riskPercent,
    upcomingNews: newsLite,
  });

  if (!risk.approved) {
    return { status: "rejected", reason: `risk_rejected: ${risk.reasons.join("; ")}`, score: aiResult.score };
  }

  const concernsLine =
    aiResult.concerns && aiResult.concerns.length > 0 ? aiResult.concerns.join("; ") : "none";

  const reasoning = [
    `Strategy ${strategyName} (${direction}):`,
    `  ${candidate.reasoning}`,
    "",
    `AI score: ${aiResult.score}`,
    `AI reasoning: ${aiResult.reasoning}`,
    `AI concerns: ${concernsLine}`,
    "",
    `Risk approved. Position size ${risk.positionSize.toFixed(8)} units.`,
  ].join("\n");

  try {
    const signal = await prisma.signal.create({
      data: {
        ...(candidate.clientId ? { id: candidate.clientId } : {}),
        symbol,
        timeframe,
        direction,
        entryPrice: candidate.entryPrice.toFixed(8),
        stopLoss: candidate.stopLoss.toFixed(8),
        takeProfit: candidate.takeProfit.toFixed(8),
        confidenceScore: Math.round(aiResult.score),
        aiReasoning: reasoning,
        strategyName,
        status: "PENDING",
      },
    });
    return { status: "generated", signalId: signal.id, score: aiResult.score };
  } catch (err) {
    if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === "P2002") {
      // Lost an idempotency race; the other writer already stored it.
      return { status: "skipped", reason: "idempotent_duplicate" };
    }
    throw err;
  }
}
