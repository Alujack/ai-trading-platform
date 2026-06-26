/**
 * Fast active position management for scalps (Phase 3 — the aggressive-scalper skill's edge).
 *
 * The 5-min reconciler (`monitorLiveTrades`) only *records* closes the broker already
 * made (SL/TP/manual). Scalps need faster, active exits BEFORE the SL — the manual
 * playbook's rules that turned slippage-hunted SL losses into small managed exits:
 *
 *   - unsafe-stop  : on first sight, if the actual fill ate more than half the
 *                    intended stop room (slippage jammed the fill toward the SL),
 *                    close — that 0.4–0.9pt-from-SL jam is a guaranteed stop-hunt.
 *   - emergency    : unrealized R <= -emergencyR in one check → close now.
 *   - two-check    : this check worse than the last AND R <= -watchR → close
 *                    (the "two consecutive adverse checks, no recovery" rule).
 *   - profit-lock  : once R reaches trailStartR, close if it gives back trailGivebackR
 *                    (don't let a +1R scalp round-trip to a loss).
 *
 * Everything is measured in R = unrealized profit ÷ the trade's risk amount, so the
 * thresholds are scale-invariant across symbols and account sizes. The loop needs
 * only `getPositions()` (live unrealized P&L) + the trade/signal rows — no candle
 * feed and no new bridge endpoints. It manages only scalp-strategy trades
 * (`SCALP_MANAGED_PREFIX`, default "scalp"); trend/swing trades are left to their
 * structural SL/TP.
 */
import { getBroker } from "./broker";
import { finalizeLiveClose, type LiveTradeWithSignal } from "./liveTrade";
import { decideScalpAction, loadConfig, type TicketState } from "./scalpDecision";
import { prisma } from "../lib/prisma";

function num(d: { toString(): string } | null | undefined): number {
  return d == null ? Number.NaN : Number(d.toString());
}

function managedPrefix(): string {
  return (process.env.SCALP_MANAGED_PREFIX ?? "scalp").trim();
}

// Per-ticket state across ticks. Cleared for tickets no longer open each tick.
const ticketStates = new Map<string, TicketState>();

/** Test-only: reset the in-memory state between cases. */
export function __resetScalpState(): void {
  ticketStates.clear();
}

export interface ScalpManageSummary {
  managed: number;
  closed: number;
  held: number;
  gone: number;
}

/**
 * One management pass over open scalp trades. Polls the broker once, decides per
 * position via `decideScalpAction`, and closes (at market) + journals via
 * `finalizeLiveClose` for any that trip a rule. Idempotent and safe to run every
 * ~15s; only fires when BROKER=exness and there are open scalp trades.
 */
export async function runScalpManagementTick(): Promise<ScalpManageSummary> {
  const cfg = loadConfig();
  const trades = (await prisma.trade.findMany({
    where: {
      status: "OPEN",
      externalOrderId: { not: null },
      signal: { strategyName: { startsWith: managedPrefix() } },
    },
    include: { signal: true },
  })) as LiveTradeWithSignal[];

  if (trades.length === 0) {
    ticketStates.clear();
    return { managed: 0, closed: 0, held: 0, gone: 0 };
  }

  const broker = getBroker();
  let positions: import("./broker").BrokerPosition[];
  try {
    positions = await broker.getPositions();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[scalpManager] getPositions failed: ${msg}`);
    return { managed: trades.length, closed: 0, held: trades.length, gone: 0 };
  }

  const byTicket = new Map(positions.map((p) => [String(p.ticket), p]));
  const liveTickets = new Set<string>();
  let closed = 0;
  let held = 0;
  let gone = 0;

  for (const trade of trades) {
    const ticket = trade.externalOrderId!;
    const pos = byTicket.get(ticket);
    if (!pos) {
      // Ticket already gone from the broker — the 5-min reconciler will record it.
      gone++;
      continue;
    }
    liveTickets.add(ticket);

    const riskAmount = num(trade.riskAmount);
    if (!(riskAmount > 0)) {
      held++;
      continue;
    }
    const r = pos.profit / riskAmount;
    const intendedStopDist = Math.abs(num(trade.signal.entryPrice) - num(trade.signal.stopLoss));
    const actualStopDist = Math.abs(pos.openPrice - pos.stopLoss);

    const decision = decideScalpAction(
      { state: ticketStates.get(ticket), r, intendedStopDist, actualStopDist },
      cfg,
    );
    ticketStates.set(ticket, decision.nextState);

    if (decision.action !== "close") {
      held++;
      continue;
    }

    let res: import("./broker").ClosePositionResult;
    try {
      res = await broker.closePosition(ticket);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[scalpManager] close failed ticket=${ticket}: ${msg}`);
      held++;
      continue;
    }

    if (res.status !== "closed") {
      // Most often "not_found" — the broker SL/TP already took it; reconciler handles it.
      console.warn(`[scalpManager] close ticket=${ticket} -> ${res.status} (${res.reason ?? "—"})`);
      held++;
      continue;
    }

    const exitPrice = res.exitPrice ?? pos.openPrice;
    const realizedProfit = res.profit ?? pos.profit;
    const { outcome } = await finalizeLiveClose(trade, exitPrice, realizedProfit, decision.reason);
    ticketStates.delete(ticket);
    closed++;
    console.log(
      `[scalpManager] ${new Date().toISOString()} closed trade=${trade.id} ` +
        `${trade.signal.symbol} ticket=${ticket} reason=${decision.reason} ` +
        `R=${r.toFixed(2)} pnl=$${realizedProfit.toFixed(2)} ${outcome}`,
    );
  }

  // Drop state for tickets no longer open (closed by us, the reconciler, or the broker).
  for (const t of [...ticketStates.keys()]) {
    if (!liveTickets.has(t)) ticketStates.delete(t);
  }

  return { managed: trades.length, closed, held, gone };
}
