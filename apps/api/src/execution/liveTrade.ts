/**
 * Live broker trade execution + position reconciliation (Plan 08, Phases 3–5).
 *
 * openLiveTrade  — size via broker tick-value formula, place via getBroker().placeOrder(),
 *                  persist Trade with externalOrderId / brokerFillPrice / broker.
 * monitorLiveTrades — poll getBroker().getPositions(), detect closed tickets,
 *                     fetch deal history, write Trade.CLOSED + Journal entry.
 *
 * This file is only called when BROKER=exness; the paper path (paperTrading.ts)
 * is unchanged and still runs when BROKER=paper.
 */
import type { Prisma } from "@prisma/client";
import { getBroker, lotsFromUnits } from "./broker";
import { prisma } from "../lib/prisma";
import { publishEvent } from "../lib/realtime";

const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";

function num(d: Prisma.Decimal | null | undefined): number {
  return d == null ? Number.NaN : Number(d.toString());
}

// --------------------------------------------------------------------------- //
// Open
// --------------------------------------------------------------------------- //

export interface OpenResult {
  status: "opened" | "skipped";
  reason?: string;
  tradeId?: string;
}

/**
 * Place a live order via the configured broker (ExnessBroker → MT5 bridge).
 *
 * Sizing: risk$ ÷ (stop-distance-in-ticks × tick-value-per-lot), then
 *   clamped to the broker's volumeMin / volumeStep / volumeMax.
 * The broker's live account balance is used so position size tracks real equity.
 */
export async function openLiveTrade(signalId: string): Promise<OpenResult> {
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
  const tp = num(signal.takeProfit);
  if (!Number.isFinite(entry) || !Number.isFinite(stop) || entry === stop) {
    return { status: "skipped", reason: "invalid_levels" };
  }

  const broker = getBroker();

  // Refuse to place if the bridge is unhealthy — positions have server-side SL/TP
  // and are safe, but we must not open new ones into a degraded connection.
  const health = await broker.health();
  if (!health.ok) {
    return { status: "skipped", reason: `broker_unhealthy: ${health.detail ?? "unknown"}` };
  }

  // Fetch live balance + symbol spec in parallel.
  let balance: number;
  let spec: import("./broker").SymbolSpec;
  try {
    const [acct, sym] = await Promise.all([
      broker.getAccount(),
      broker.getSymbolSpec(signal.symbol),
    ]);
    balance = acct.balance;
    spec = sym;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { status: "skipped", reason: `broker_spec_error: ${msg}` };
  }

  const riskPercent = Number(process.env.PAPER_RISK_PERCENT ?? "1");
  const riskAmount = balance * (riskPercent / 100);

  // Correct lot sizing: risk$ ÷ (stop-distance-in-ticks × tick-value-per-lot)
  const stopTicks = Math.abs(entry - stop) / spec.point;
  const rawLots =
    stopTicks > 0 && spec.tickValue > 0
      ? riskAmount / (stopTicks * spec.tickValue)
      : 0;
  // lotsFromUnits expects "units" (rawLots × contractSize) and returns step-clamped lots.
  const lots = lotsFromUnits(rawLots * spec.contractSize, spec);

  if (lots <= 0) {
    return {
      status: "skipped",
      reason: `lots_below_minimum (rawLots=${rawLots.toFixed(6)} volumeMin=${spec.volumeMin})`,
    };
  }

  let result: import("./broker").PlaceOrderResult;
  try {
    result = await broker.placeOrder({
      symbol: signal.symbol,
      side: signal.direction,
      lots,
      stopLoss: stop,
      takeProfit: tp,
      clientTag: signalId,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { status: "skipped", reason: `order_error: ${msg}` };
  }

  if (result.status !== "filled") {
    return { status: "skipped", reason: `order_rejected: ${result.reason ?? "unknown"}` };
  }

  const fillPrice = result.fillPrice ?? entry;

  const [, trade] = await prisma.$transaction([
    prisma.signal.update({ where: { id: signal.id }, data: { status: "ACTIVE" } }),
    prisma.trade.create({
      data: {
        signalId: signal.id,
        entryPrice: fillPrice.toFixed(8),
        positionSize: lots.toFixed(8),
        riskAmount: riskAmount.toFixed(2),
        status: "OPEN",
        externalOrderId: result.ticket != null ? String(result.ticket) : null,
        brokerFillPrice: fillPrice.toFixed(8),
        broker: broker.name,
      },
    }),
  ]);

  console.log(
    `[liveTrade] opened trade=${trade.id} signal=${signalId} ` +
      `${signal.symbol}/${signal.direction} lots=${lots} ticket=${result.ticket ?? "?"}`,
  );
  return { status: "opened", tradeId: trade.id };
}

// --------------------------------------------------------------------------- //
// Monitor / reconcile
// --------------------------------------------------------------------------- //

export interface MonitorSummary {
  inspected: number;
  closed: number;
  unchanged: number;
}

interface TradeReviewResponse {
  grade: string;
  outcome: string;
  why: string;
  whatWorked: string[];
  whatFailed: string[];
  lesson: string;
}

async function reviewLiveClose(args: {
  symbol: string;
  direction: string;
  timeframe: string;
  strategyName: string | null;
  aiReasoning: string;
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  exitPrice: number;
  profitLoss: number;
  rMultiple: number;
  openedAt: Date;
  closedAt: Date;
}): Promise<TradeReviewResponse | null> {
  try {
    const res = await fetch(`${AI_SERVICE_URL}/analyze/trade-review`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        trade: {
          symbol: args.symbol,
          direction: args.direction,
          strategyName: args.strategyName,
          entryPrice: args.entryPrice,
          stopLoss: args.stopLoss,
          takeProfit: args.takeProfit,
          exitPrice: args.exitPrice,
          profitLoss: args.profitLoss,
          rMultiple: args.rMultiple,
          exitReason: "broker_close",
          openedAt: args.openedAt.toISOString(),
          closedAt: args.closedAt.toISOString(),
          plannedReasoning: args.aiReasoning,
        },
        candles: [],
        indicators: [],
      }),
      signal: AbortSignal.timeout(15_000),
    });
    if (!res.ok) return null;
    return (await res.json()) as TradeReviewResponse;
  } catch {
    return null;
  }
}

/**
 * Reconcile open trades (externalOrderId set) against live broker positions.
 *
 * If a ticket is gone from the broker's open-position list, the broker closed it
 * (SL/TP hit or manual close in the terminal). We fetch deal history, write the
 * Trade.CLOSED record, and create a Journal entry with an AI grade.
 *
 * Called every 5 minutes from the scheduler when BROKER=exness.
 */
export async function monitorLiveTrades(): Promise<MonitorSummary> {
  const openTrades = await prisma.trade.findMany({
    where: { status: "OPEN", externalOrderId: { not: null } },
    include: { signal: true },
  });

  if (openTrades.length === 0) return { inspected: 0, closed: 0, unchanged: 0 };

  const broker = getBroker();

  let livePositions: import("./broker").BrokerPosition[];
  try {
    livePositions = await broker.getPositions();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[liveTrade] getPositions failed: ${msg}`);
    return { inspected: openTrades.length, closed: 0, unchanged: openTrades.length };
  }

  const openTickets = new Set(livePositions.map((p) => String(p.ticket)));
  let closed = 0;
  let unchanged = 0;

  for (const trade of openTrades) {
    const ticket = trade.externalOrderId!;

    if (openTickets.has(ticket)) {
      unchanged++;
      continue;
    }

    // Ticket is gone — fetch deal history for the accurate close price and P&L.
    let exitPrice: number = num(trade.entryPrice); // safe fallback
    let realizedProfit = 0;

    if (broker.getPositionHistory) {
      try {
        const hist = await broker.getPositionHistory(ticket);
        if (hist?.found && hist.exitPrice != null) {
          exitPrice = hist.exitPrice;
          realizedProfit = hist.profit ?? 0;
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`[liveTrade] history fetch failed ticket=${ticket}: ${msg}`);
      }
    }

    const sig = trade.signal;
    const entry = num(trade.entryPrice);
    const riskAmount = num(trade.riskAmount);
    const lots = num(trade.positionSize);
    const rMultiple = riskAmount > 0 ? realizedProfit / riskAmount : 0;
    const outcome = realizedProfit > 0 ? "WIN" : realizedProfit < 0 ? "LOSS" : "BREAKEVEN";
    const closedAt = new Date();

    const review = await reviewLiveClose({
      symbol: sig.symbol,
      direction: sig.direction,
      timeframe: sig.timeframe,
      strategyName: sig.strategyName,
      aiReasoning: sig.aiReasoning,
      entryPrice: entry,
      stopLoss: num(sig.stopLoss),
      takeProfit: num(sig.takeProfit),
      exitPrice,
      profitLoss: realizedProfit,
      rMultiple,
      openedAt: trade.openedAt,
      closedAt,
    });

    const notes =
      `Live close reconciled by MT5 bridge. ${sig.direction} ${sig.symbol} ${sig.timeframe}. ` +
      `Outcome: ${outcome}. Entry ${entry.toFixed(5)} → Exit ${exitPrice.toFixed(5)}. ` +
      `Lots ${lots.toFixed(4)}, P&L $${realizedProfit.toFixed(2)}, R ${rMultiple.toFixed(2)}. ` +
      `MT5 ticket #${ticket}.`;

    const aiReview = review
      ? `Grade ${review.grade} (${review.outcome}). ${review.why} ` +
        `Worked: ${review.whatWorked.join("; ") || "—"}. ` +
        `Failed: ${review.whatFailed.join("; ") || "—"}. ` +
        `Lesson: ${review.lesson}`
      : "(per-trade AI review unavailable at live close)";

    await prisma.$transaction([
      prisma.trade.update({
        where: { id: trade.id },
        data: {
          exitPrice: exitPrice.toFixed(8),
          profitLoss: realizedProfit.toFixed(2),
          status: "CLOSED",
          closedAt,
        },
      }),
      prisma.signal.update({ where: { id: sig.id }, data: { status: "CLOSED" } }),
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

    void publishEvent({ type: "trade", symbol: sig.symbol });
    console.log(
      `[liveTrade] reconciled close trade=${trade.id} ${sig.symbol} ticket=${ticket} ` +
        `${outcome} pnl=$${realizedProfit.toFixed(2)} R=${rMultiple.toFixed(2)} ` +
        `grade=${review?.grade ?? "n/a"}`,
    );
    closed++;
  }

  return { inspected: openTrades.length, closed, unchanged };
}
