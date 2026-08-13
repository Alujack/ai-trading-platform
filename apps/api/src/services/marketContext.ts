/**
 * Market-context builder: assembles recent candles + indicators + upcoming
 * news for a (symbol, timeframe) and asks the AI service for a structured
 * briefing (bias, key levels, risks). Shared by the GET /api/market-context
 * route and the 06:00 UTC daily briefing (plan 10, Phase 3 market-context
 * agent), with a Redis cache so both paths reuse one AI call.
 */

import { Prisma } from "@prisma/client";
import { HttpError } from "../errors/httpError";
import { prisma } from "../lib/prisma";
import { redis } from "../lib/redis";

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";
const CACHE_TTL_SECONDS = 10 * 60;
const CANDLE_LOOKBACK = 50;
const NEWS_LOOKAHEAD = 8;

export type Bias = "Bullish" | "Bearish" | "Neutral";

interface AiMarketContextResponse {
  bias: Bias;
  summary: string;
  keyLevels: string[];
  risks: string[];
}

export interface MarketContextPayload extends AiMarketContextResponse {
  symbol: string;
  timeframe: string;
  generatedAt: string;
  cached: boolean;
}

function num(d: Prisma.Decimal | null | undefined): number | null {
  if (d == null) return null;
  const n = Number(d.toString());
  return Number.isFinite(n) ? n : null;
}

function cacheKey(symbol: string, timeframe: string): string {
  return `market-context:${symbol}:${timeframe}`;
}

async function readCache(key: string): Promise<MarketContextPayload | null> {
  if (redis.status !== "ready") return null;
  try {
    const raw = await redis.get(key);
    if (!raw) return null;
    return { ...(JSON.parse(raw) as MarketContextPayload), cached: true };
  } catch {
    return null;
  }
}

async function writeCache(key: string, payload: MarketContextPayload): Promise<void> {
  if (redis.status !== "ready") return;
  try {
    await redis.setex(key, CACHE_TTL_SECONDS, JSON.stringify(payload));
  } catch {
    // Cache write is best-effort; a Redis hiccup must not fail the request.
  }
}

/** Build (or reuse from cache) the market-context briefing for one series. */
export async function getMarketContext(
  symbol: string,
  timeframe: string,
): Promise<MarketContextPayload> {
  const key = cacheKey(symbol, timeframe);
  const cached = await readCache(key);
  if (cached) return cached;

  const candles = await prisma.candle.findMany({
    where: { symbol, timeframe },
    orderBy: { timestamp: "desc" },
    take: CANDLE_LOOKBACK,
  });
  if (candles.length === 0) {
    throw new HttpError(404, `No candles for ${symbol}/${timeframe}`);
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
    symbol,
    timeframe,
    candles: candles.map((c) => ({
      timestamp: c.timestamp.toISOString(),
      open: num(c.open),
      high: num(c.high),
      low: num(c.low),
      close: num(c.close),
      volume: num(c.volume),
    })),
    indicators: indicators.map((i) => ({
      timestamp: i.timestamp.toISOString(),
      rsi: num(i.rsi),
      ema20: num(i.ema20),
      ema50: num(i.ema50),
      ema200: num(i.ema200),
      atr: num(i.atr),
    })),
    news: upcomingNews.map((n) => ({
      title: n.title,
      impact: n.impact,
      currency: n.currency,
      scheduledAt: n.scheduledAt.toISOString(),
    })),
  };

  let ai: AiMarketContextResponse;
  try {
    const aiRes = await fetch(`${AI_SERVICE_URL}/analyze/market-context`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(aiBody),
    });
    if (!aiRes.ok) {
      const detail = await aiRes.text().catch(() => "");
      throw new HttpError(502, `AI service ${aiRes.status}: ${detail.slice(0, 160)}`);
    }
    ai = (await aiRes.json()) as AiMarketContextResponse;
  } catch (err) {
    if (err instanceof HttpError) throw err;
    const msg = err instanceof Error ? err.message : String(err);
    throw new HttpError(503, `AI service unreachable: ${msg}`);
  }

  const payload: MarketContextPayload = {
    symbol,
    timeframe,
    bias: ai.bias,
    summary: ai.summary,
    keyLevels: ai.keyLevels ?? [],
    risks: ai.risks ?? [],
    generatedAt: new Date().toISOString(),
    cached: false,
  };

  await writeCache(key, payload);
  return payload;
}
