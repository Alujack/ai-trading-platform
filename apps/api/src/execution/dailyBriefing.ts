import { prisma } from "../lib/prisma";
import { redis } from "../lib/redis";
import { getMarketContext, type MarketContextPayload } from "../services/marketContext";
import { computePerformance, type TradeStats } from "../services/performance";
import { tradedPairs } from "./dataFreshness";

const DAY_MS = 24 * 60 * 60 * 1000;

export interface BriefingTrade {
  symbol: string;
  direction: string;
  outcome: string | null;
  grade: string | null;
  rMultiple: number | null;
  lesson: string | null;
  closedAt: string | null;
}

export interface DailyBriefing {
  generatedAt: string;
  performance: {
    totalTrades: number;
    winRate: number;
    totalPnL: number;
    maxDrawdown: number;
    averageRR: number;
    expectancy: number;
    profitFactor: number;
    rExpectancy: number; // mean R-multiple from graded journals (the edge metric)
    gradedTrades: number;
  };
  gradeDistribution: Record<string, number>;
  recent24h: BriefingTrade[];
  upcomingHighImpactNews: { title: string; currency: string; scheduledAt: string }[];
  topLessons: string[];
  // Phase 3 market-context agent: the AI's morning read (bias, levels, risks)
  // for each traded series. Empty when the AI service is unreachable.
  marketContext: MarketContextPayload[];
}

/** Market context for every traded series — best-effort, never throws. */
export async function collectMarketContext(): Promise<MarketContextPayload[]> {
  const out: MarketContextPayload[] = [];
  try {
    for (const { symbol, timeframe } of await tradedPairs()) {
      try {
        out.push(await getMarketContext(symbol, timeframe));
      } catch (err) {
        console.warn(
          `[dailyBriefing] market context unavailable for ${symbol}/${timeframe}: ${err instanceof Error ? err.message : err}`,
        );
      }
    }
  } catch (err) {
    console.warn(
      `[dailyBriefing] market context skipped: ${err instanceof Error ? err.message : err}`,
    );
  }
  return out;
}

/**
 * The agent's morning routine: summarize how it has been trading before the day
 * starts — performance + expectancy, the grades/lessons from recently closed
 * trades, and the high-impact news ahead. Persists `briefing:latest` to Redis
 * for the dashboard. Best-effort: never throws into the startup path.
 */
export async function runDailyBriefing(): Promise<DailyBriefing> {
  const now = new Date();
  const since24h = new Date(now.getTime() - DAY_MS);

  const closed = await prisma.trade.findMany({
    where: { status: "CLOSED" },
    include: { signal: true, journals: { orderBy: { createdAt: "desc" }, take: 1 } },
    orderBy: { closedAt: "desc" },
  });

  const stats: TradeStats[] = closed.map((t) => ({
    entryPrice: Number(t.entryPrice),
    exitPrice: t.exitPrice ? Number(t.exitPrice) : null,
    profitLoss: t.profitLoss ? Number(t.profitLoss) : null,
    direction: t.signal.direction as "LONG" | "SHORT",
    stopLoss: Number(t.signal.stopLoss),
  }));
  const perf = computePerformance(stats);

  // R-expectancy + grade distribution from the per-trade journal reviews.
  const rmults: number[] = [];
  const gradeDistribution: Record<string, number> = {};
  for (const t of closed) {
    const j = t.journals[0];
    if (!j) continue;
    if (j.rMultiple != null) rmults.push(Number(j.rMultiple));
    if (j.grade) gradeDistribution[j.grade] = (gradeDistribution[j.grade] ?? 0) + 1;
  }
  const rExpectancy = rmults.length ? rmults.reduce((a, b) => a + b, 0) / rmults.length : 0;

  const recent24h: BriefingTrade[] = closed
    .filter((t) => t.closedAt && t.closedAt >= since24h)
    .map((t) => {
      const j = t.journals[0];
      return {
        symbol: t.signal.symbol,
        direction: t.signal.direction,
        outcome: j?.outcome ?? null,
        grade: j?.grade ?? null,
        rMultiple: j?.rMultiple != null ? Number(j.rMultiple) : null,
        lesson: j?.lesson ?? null,
        closedAt: t.closedAt?.toISOString() ?? null,
      };
    });

  const news = await prisma.newsEvent.findMany({
    where: { scheduledAt: { gt: now, lt: new Date(now.getTime() + DAY_MS) }, impact: "HIGH" },
    orderBy: { scheduledAt: "asc" },
    take: 10,
  });

  const topLessons = closed
    .map((t) => t.journals[0]?.lesson)
    .filter((l): l is string => Boolean(l))
    .slice(0, 5);

  const marketContext = await collectMarketContext();

  const briefing: DailyBriefing = {
    generatedAt: now.toISOString(),
    performance: {
      ...perf,
      rExpectancy: Math.round(rExpectancy * 1000) / 1000,
      gradedTrades: rmults.length,
    },
    gradeDistribution,
    recent24h,
    upcomingHighImpactNews: news.map((n) => ({
      title: n.title,
      currency: n.currency,
      scheduledAt: n.scheduledAt.toISOString(),
    })),
    topLessons,
    marketContext,
  };

  try {
    if (redis.status === "ready") {
      await redis.set("briefing:latest", JSON.stringify(briefing));
    }
  } catch {
    // dashboard cache is best-effort
  }

  console.log(
    `[dailyBriefing] ${now.toISOString()} trades=${perf.totalTrades} win=${perf.winRate}% ` +
      `expectancy=$${perf.expectancy} R=${briefing.performance.rExpectancy} PF=${perf.profitFactor} ` +
      `recent24h=${recent24h.length} highImpactNews=${news.length} ` +
      `marketContext=${marketContext.map((m) => `${m.symbol}/${m.timeframe}:${m.bias}`).join(",") || "none"}`,
  );
  return briefing;
}
