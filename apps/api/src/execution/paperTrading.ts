import type { Prisma } from "@prisma/client";
import { prisma } from "../lib/prisma";
import { redis } from "../lib/redis";
import { calculatePositionSize } from "../risk/riskEngine";

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";
const WEEKLY_REVIEW_WINDOW_DAYS = 7;
const WEEKLY_REVIEW_MAX_TRADES = 100;

function num(d: Prisma.Decimal | null | undefined): number {
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

async function fetchCurrentPrice(symbol: string, timeframe: string): Promise<number | null> {
  try {
    if (redis.status === "ready") {
      const cached = await redis.get(`price:${symbol}`);
      if (cached) {
        const n = Number(cached);
        if (Number.isFinite(n) && n > 0) return n;
      }
    }
  } catch {
    // fall through to candle
  }
  const candle = await prisma.candle.findFirst({
    where: { symbol, timeframe },
    orderBy: { timestamp: "desc" },
    select: { close: true },
  });
  if (!candle) return null;
  const n = num(candle.close);
  return Number.isFinite(n) ? n : null;
}

export interface OpenResult {
  status: "opened" | "skipped";
  reason?: string;
  tradeId?: string;
}

export async function openPaperTrade(signalId: string): Promise<OpenResult> {
  const signal = await prisma.signal.findUnique({
    where: { id: signalId },
    include: { trades: { select: { id: true } } },
  });
  if (!signal) return { status: "skipped", reason: "signal_not_found" };
  if (signal.trades.length > 0) return { status: "skipped", reason: "already_has_trade" };
  if (signal.status !== "PENDING") {
    return { status: "skipped", reason: `signal_status_${signal.status}` };
  }

  const entry = num(signal.entryPrice);
  const stop = num(signal.stopLoss);
  if (!Number.isFinite(entry) || !Number.isFinite(stop) || entry === stop) {
    return { status: "skipped", reason: "invalid_levels" };
  }

  const account = readAccountState();
  let sized;
  try {
    sized = calculatePositionSize(account.accountBalance, account.riskPercent, entry, stop);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { status: "skipped", reason: `position_size_error: ${msg}` };
  }

  const [, trade] = await prisma.$transaction([
    prisma.signal.update({
      where: { id: signal.id },
      data: { status: "ACTIVE" },
    }),
    prisma.trade.create({
      data: {
        signalId: signal.id,
        entryPrice: entry.toFixed(8),
        positionSize: sized.lotSize.toFixed(8),
        riskAmount: sized.riskAmount.toFixed(2),
        status: "OPEN",
      },
    }),
  ]);

  return { status: "opened", tradeId: trade.id };
}

export interface SweepSummary {
  scanned: number;
  opened: number;
  skipped: number;
}

export async function sweepPendingSignals(): Promise<SweepSummary> {
  const pending = await prisma.signal.findMany({
    where: { status: "PENDING", trades: { none: {} } },
    orderBy: { createdAt: "asc" },
    take: 50,
  });
  let opened = 0;
  let skipped = 0;
  for (const sig of pending) {
    const r = await openPaperTrade(sig.id);
    if (r.status === "opened") {
      opened += 1;
      console.log(`[paperTrading] opened trade ${r.tradeId} for signal ${sig.id} ${sig.symbol}/${sig.timeframe}`);
    } else {
      skipped += 1;
      console.log(
        `[paperTrading] skip signal ${sig.id} (${sig.symbol}/${sig.timeframe}) reason="${r.reason ?? ""}"`,
      );
    }
  }
  return { scanned: pending.length, opened, skipped };
}

export interface MonitorSummary {
  inspected: number;
  closed: number;
  unchanged: number;
  noPrice: number;
}

export interface CloseDecision {
  exitPrice: number;
  outcome: "win" | "loss";
}

export function evaluateExit(
  direction: "LONG" | "SHORT",
  price: number,
  takeProfit: number,
  stopLoss: number,
): CloseDecision | null {
  if (direction === "LONG") {
    if (price <= stopLoss) return { exitPrice: stopLoss, outcome: "loss" };
    if (price >= takeProfit) return { exitPrice: takeProfit, outcome: "win" };
    return null;
  }
  if (price >= stopLoss) return { exitPrice: stopLoss, outcome: "loss" };
  if (price <= takeProfit) return { exitPrice: takeProfit, outcome: "win" };
  return null;
}

interface TradeReviewResponse {
  grade: string;
  outcome: string;
  why: string;
  whatWorked: string[];
  whatFailed: string[];
  lesson: string;
}

/**
 * Grade a just-closed trade on PROCESS (not P&L) and extract one lesson, via the
 * AI service's /analyze/trade-review. Best-effort: a review failure must never
 * block the trade close, so this returns null and the close proceeds.
 */
async function reviewClosedTrade(args: {
  sig: {
    symbol: string;
    timeframe: string;
    direction: string;
    stopLoss: Prisma.Decimal;
    takeProfit: Prisma.Decimal;
    strategyName: string | null;
    aiReasoning: string;
  };
  entry: number;
  exitPrice: number;
  pnl: number;
  rMultiple: number;
  exitReason: string;
  openedAt: Date;
  closedAt: Date;
}): Promise<TradeReviewResponse | null> {
  try {
    const res = await fetch(`${AI_SERVICE_URL}/analyze/trade-review`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        trade: {
          symbol: args.sig.symbol,
          direction: args.sig.direction,
          strategyName: args.sig.strategyName,
          entryPrice: args.entry,
          stopLoss: num(args.sig.stopLoss),
          takeProfit: num(args.sig.takeProfit),
          exitPrice: args.exitPrice,
          profitLoss: args.pnl,
          rMultiple: args.rMultiple,
          exitReason: args.exitReason,
          openedAt: args.openedAt.toISOString(),
          closedAt: args.closedAt.toISOString(),
          plannedReasoning: args.sig.aiReasoning,
        },
        candles: [],
        indicators: [],
      }),
    });
    if (!res.ok) {
      console.error(`[paperTrading] trade-review failed http_${res.status}`);
      return null;
    }
    return (await res.json()) as TradeReviewResponse;
  } catch (err) {
    console.error(
      `[paperTrading] trade-review unreachable: ${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
}

export async function monitorOpenTrades(): Promise<MonitorSummary> {
  const open = await prisma.trade.findMany({
    where: { status: "OPEN" },
    include: { signal: true },
  });

  let closed = 0;
  let unchanged = 0;
  let noPrice = 0;

  for (const trade of open) {
    const sig = trade.signal;
    const price = await fetchCurrentPrice(sig.symbol, sig.timeframe);
    if (price == null) {
      noPrice += 1;
      console.log(
        `[paperTrading] no_price trade=${trade.id} ${sig.symbol}/${sig.timeframe} — skipping`,
      );
      continue;
    }

    const decision = evaluateExit(
      sig.direction,
      price,
      num(sig.takeProfit),
      num(sig.stopLoss),
    );
    if (!decision) {
      unchanged += 1;
      continue;
    }

    const entry = num(trade.entryPrice);
    const size = num(trade.positionSize);
    const directionSign = sig.direction === "LONG" ? 1 : -1;
    const pnl = (decision.exitPrice - entry) * size * directionSign;

    // R-multiple (net P&L ÷ risk) and a deterministic outcome — both computed
    // here so expectancy tracking works even if the AI review is unavailable.
    const riskAmount = num(trade.riskAmount);
    const rMultiple = Number.isFinite(riskAmount) && riskAmount > 0 ? pnl / riskAmount : 0;
    const outcome = pnl > 0 ? "WIN" : pnl < 0 ? "LOSS" : "BREAKEVEN";
    const closedAt = new Date();

    // Per-trade learning loop: grade the process + extract a lesson (best-effort).
    const review = await reviewClosedTrade({
      sig,
      entry,
      exitPrice: decision.exitPrice,
      pnl,
      rMultiple,
      exitReason: decision.outcome,
      openedAt: trade.openedAt,
      closedAt,
    });

    const notes =
      `Auto-closed by paper trading engine. ${sig.direction} ${sig.symbol} ${sig.timeframe}. ` +
      `Outcome: ${outcome}. ` +
      `Entry ${entry.toFixed(5)} → Exit ${decision.exitPrice.toFixed(5)}. ` +
      `Size ${size.toFixed(4)}, P&L $${pnl.toFixed(2)}, R ${rMultiple.toFixed(2)}.`;

    const aiReview = review
      ? `Grade ${review.grade} (${review.outcome}). ${review.why} ` +
        `Worked: ${review.whatWorked.join("; ") || "—"}. ` +
        `Failed: ${review.whatFailed.join("; ") || "—"}. ` +
        `Lesson: ${review.lesson}`
      : "(per-trade AI review unavailable at close)";

    await prisma.$transaction([
      prisma.trade.update({
        where: { id: trade.id },
        data: {
          exitPrice: decision.exitPrice.toFixed(8),
          profitLoss: pnl.toFixed(2),
          status: "CLOSED",
          closedAt,
        },
      }),
      prisma.signal.update({
        where: { id: sig.id },
        data: { status: "CLOSED" },
      }),
      prisma.journal.create({
        data: {
          tradeId: trade.id,
          notes,
          aiReview,
          grade: review?.grade ?? null,
          outcome,
          lesson: review?.lesson ?? null,
          rMultiple: rMultiple.toFixed(4),
        },
      }),
    ]);

    closed += 1;
    console.log(
      `[paperTrading] closed trade=${trade.id} ${sig.symbol}/${sig.timeframe} ` +
        `${outcome} pnl=$${pnl.toFixed(2)} R=${rMultiple.toFixed(2)} grade=${review?.grade ?? "n/a"}`,
    );
  }

  return { inspected: open.length, closed, unchanged, noPrice };
}

export interface WeeklyReviewResult {
  status: "ok" | "skipped" | "error";
  reason?: string;
  tradeCount?: number;
  patterns?: string[];
  strengths?: string[];
  weaknesses?: string[];
  suggestions?: string[];
}

interface JournalReviewResponse {
  patterns: string[];
  strengths: string[];
  weaknesses: string[];
  suggestions: string[];
}

export async function runWeeklyJournalReview(): Promise<WeeklyReviewResult> {
  const cutoff = new Date(Date.now() - WEEKLY_REVIEW_WINDOW_DAYS * 24 * 60 * 60 * 1000);
  const trades = await prisma.trade.findMany({
    where: { status: "CLOSED", closedAt: { gte: cutoff } },
    include: { signal: true, journals: { orderBy: { createdAt: "asc" }, take: 1 } },
    orderBy: { closedAt: "asc" },
    take: WEEKLY_REVIEW_MAX_TRADES,
  });

  if (trades.length === 0) {
    console.log("[paperTrading] weekly review skipped — no closed trades in last 7 days");
    return { status: "skipped", reason: "no_trades", tradeCount: 0 };
  }

  const payload = {
    trades: trades.map((t) => {
      const journal = t.journals[0];
      return {
        symbol: t.signal.symbol,
        direction: t.signal.direction,
        entryPrice: num(t.entryPrice),
        exitPrice: t.exitPrice ? num(t.exitPrice) : null,
        profitLoss: t.profitLoss ? num(t.profitLoss) : null,
        openedAt: t.openedAt.toISOString(),
        closedAt: t.closedAt?.toISOString() ?? null,
        notes: journal?.notes ?? "",
        emotions: journal?.emotions ?? null,
        aiReview: journal?.aiReview ?? null,
      };
    }),
  };

  try {
    const res = await fetch(`${AI_SERVICE_URL}/analyze/journal-review`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      console.error(
        `[paperTrading] weekly review failed http_${res.status}: ${errText.slice(0, 200)}`,
      );
      return { status: "error", reason: `http_${res.status}`, tradeCount: trades.length };
    }
    const review = (await res.json()) as JournalReviewResponse;
    console.log(
      `[paperTrading] weekly review ok trades=${trades.length} patterns=${review.patterns.length} suggestions=${review.suggestions.length}`,
    );
    console.log("[paperTrading] weekly review result:", JSON.stringify(review));
    return { status: "ok", tradeCount: trades.length, ...review };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[paperTrading] weekly review unreachable: ${msg}`);
    return { status: "error", reason: `unreachable: ${msg}`, tradeCount: trades.length };
  }
}
