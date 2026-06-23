import { Prisma } from "@prisma/client";
import { Router } from "express";
import { HttpError } from "../errors/httpError";
import { prisma } from "../lib/prisma";
import { redis } from "../lib/redis";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";
import {
  marketContextQuerySchema,
  type MarketContextQuery,
} from "../schemas/marketContext.schema";

const router = Router();

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";
const CACHE_TTL_SECONDS = 10 * 60;
const CANDLE_LOOKBACK = 50;
const NEWS_LOOKAHEAD = 8;

type Bias = "Bullish" | "Bearish" | "Neutral";

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

router.get(
  "/market-context",
  validate(marketContextQuerySchema, "query"),
  asyncHandler(async (req, res) => {
    const { symbol, timeframe } = req.query as unknown as MarketContextQuery;
    const key = cacheKey(symbol, timeframe);

    const cached = await readCache(key);
    if (cached) {
      res.json(cached);
      return;
    }

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
    res.json(payload);
  }),
);

export default router;
