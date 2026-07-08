import type { Signal } from "@prisma/client";
import { SYMBOL_CURRENCIES } from "../config/defaults";
import { resolveExecutionMode, resolveRiskConfig } from "../config/resolve";
import { prisma } from "../lib/prisma";
import { requestApproval } from "../telegram/approvals";
import { openLiveTrade } from "./liveTrade";
import { openPaperTrade } from "./paperTrading";

/** True when BROKER=exness (live MT5 execution). */
function isLiveBroker(): boolean {
  return (process.env.BROKER ?? "paper").trim().toLowerCase() === "exness";
}

/**
 * The execution decider — the single new branch between the gate and execution.
 * It applies the effective ExecutionMode (OFF / AUTO / CONFIRM) and the pre-trade
 * portfolio caps (max open trades, max open risk, per-currency exposure). It runs
 * AFTER the risk engine has already approved the signal; it can only hold or route
 * a trade, never loosen a risk check.
 */

export type DecisionAction = "opened" | "awaiting_approval" | "held_off" | "blocked";

export interface Decision {
  mode: string;
  action: DecisionAction;
  reason?: string;
  tradeId?: string;
}

function readAccount() {
  return {
    accountBalance: Number(process.env.PAPER_ACCOUNT_BALANCE ?? "10000"),
    peakBalance: Number(process.env.PAPER_PEAK_BALANCE ?? "10000"),
  };
}

function num(v: { toString(): string } | null | undefined): number {
  return v == null ? 0 : Number(v.toString());
}

/** Today's realized P&L (UTC): net, and gross loss as a positive number. */
async function todayRealizedPnl(): Promise<{ net: number; loss: number }> {
  const startOfDay = new Date();
  startOfDay.setUTCHours(0, 0, 0, 0);
  const trades = await prisma.trade.findMany({
    where: { status: "CLOSED", closedAt: { gte: startOfDay } },
    select: { profitLoss: true },
  });
  let net = 0;
  let loss = 0;
  for (const t of trades) {
    const pl = num(t.profitLoss);
    net += pl;
    if (pl < 0) loss += Math.abs(pl);
  }
  return { net, loss };
}

/**
 * Has a circuit breaker tripped today? When true, the decider forces effective
 * OFF regardless of any AUTO setting (breaker > mode, Part C §4.2 / §6).
 */
export async function isBreakerTrippedToday(): Promise<{ tripped: boolean; reason?: string }> {
  const cfg = await resolveRiskConfig();
  const { accountBalance, peakBalance } = readAccount();

  const { net, loss } = await todayRealizedPnl();
  const dailyLimit = accountBalance * (cfg.dailyLossLimitPct / 100);
  if (loss > dailyLimit) {
    return { tripped: true, reason: `daily-loss ${loss.toFixed(0)} > limit ${dailyLimit.toFixed(0)}` };
  }

  // Profit target: bank the green day. Realized-only, so an open trade still
  // runs to its own exit — the breaker only stops NEW trades until next UTC day.
  const profitTarget = accountBalance * (cfg.dailyProfitTargetPct / 100);
  if (net >= profitTarget) {
    return {
      tripped: true,
      reason: `daily profit target hit (+$${net.toFixed(2)} >= $${profitTarget.toFixed(2)}) — done for today`,
    };
  }

  // Drawdown from peak using realized equity.
  const realized = await prisma.trade.aggregate({
    where: { status: "CLOSED" },
    _sum: { profitLoss: true },
  });
  const equity = accountBalance + num(realized._sum.profitLoss);
  if (peakBalance > 0) {
    const ddPct = ((peakBalance - equity) / peakBalance) * 100;
    if (ddPct > cfg.maxDrawdownPct) {
      return { tripped: true, reason: `drawdown ${ddPct.toFixed(1)}% > max ${cfg.maxDrawdownPct}%` };
    }
  }
  return { tripped: false };
}

/**
 * Pre-trade portfolio caps the decider enforces before opening (or requesting
 * approval for) a new trade. Returns a reason string when a cap is hit, else null.
 */
async function portfolioCapBlock(signal: Signal): Promise<string | null> {
  const cfg = await resolveRiskConfig(signal.strategyName, signal.symbol);
  const { accountBalance } = readAccount();

  // Frequency caps are PER-STRATEGY: each strategy's maxTradesPerDay /
  // maxOpenTrades meters its own trades (a stacking scalper taking its 5th add
  // must not consume a swing strategy's one-a-day shot, and vice versa).
  // Exposure caps below (open risk, per-currency) stay PORTFOLIO-WIDE.
  const startOfDay = new Date();
  startOfDay.setUTCHours(0, 0, 0, 0);
  const openedToday = await prisma.trade.count({
    where: { openedAt: { gte: startOfDay }, signal: { strategyName: signal.strategyName } },
  });
  if (openedToday >= cfg.maxTradesPerDay) {
    return `daily trade limit reached (${openedToday}/${cfg.maxTradesPerDay}) — stopped for today`;
  }

  const open = await prisma.trade.findMany({
    where: { status: "OPEN" },
    include: { signal: { select: { symbol: true, strategyName: true } } },
  });

  const openSameStrategy = open.filter((t) => t.signal.strategyName === signal.strategyName);
  if (openSameStrategy.length >= cfg.maxOpenTrades) {
    return `max open trades reached (${openSameStrategy.length}/${cfg.maxOpenTrades})`;
  }

  const thisRisk = accountBalance * (cfg.riskPerTradePct / 100);
  const openRisk = open.reduce((s, t) => s + num(t.riskAmount), 0);
  const maxOpenRisk = accountBalance * (cfg.maxOpenRiskPct / 100);
  if (openRisk + thisRisk > maxOpenRisk + 1e-6) {
    return `max open risk reached ($${(openRisk + thisRisk).toFixed(0)} > $${maxOpenRisk.toFixed(0)})`;
  }

  // Per-currency exposure: sum risk by base/quote currency of open positions.
  const perCurrency = new Map<string, number>();
  for (const t of open) {
    for (const ccy of SYMBOL_CURRENCIES[t.signal.symbol] ?? []) {
      perCurrency.set(ccy, (perCurrency.get(ccy) ?? 0) + num(t.riskAmount));
    }
  }
  const maxPerCcy = accountBalance * (cfg.maxRiskPerCurrencyPct / 100);
  for (const ccy of SYMBOL_CURRENCIES[signal.symbol] ?? []) {
    if ((perCurrency.get(ccy) ?? 0) + thisRisk > maxPerCcy + 1e-6) {
      return `per-currency cap on ${ccy} ($${maxPerCcy.toFixed(0)})`;
    }
  }

  return null;
}

/**
 * Decide what to do with a freshly-persisted PENDING signal.
 *  OFF     → leave PENDING, logged, no trade (resumable within TTL)
 *  AUTO    → open immediately (today's behaviour), subject to portfolio caps
 *  CONFIRM → create Approval + send a Telegram alert
 */
export async function decideExecution(signal: Signal): Promise<Decision> {
  let mode = await resolveExecutionMode(signal.strategyName, signal.symbol);

  const breaker = await isBreakerTrippedToday();
  if (breaker.tripped) {
    mode = "OFF";
    return { mode, action: "held_off", reason: `breaker: ${breaker.reason}` };
  }

  if (mode === "OFF") {
    return { mode, action: "held_off", reason: "mode OFF" };
  }

  // Portfolio caps apply to AUTO and CONFIRM alike — block before acting.
  const cap = await portfolioCapBlock(signal);
  if (cap) {
    return { mode, action: "blocked", reason: cap };
  }

  if (mode === "AUTO") {
    const r = isLiveBroker()
      ? await openLiveTrade(signal.id)
      : await openPaperTrade(signal.id);
    return {
      mode,
      action: r.status === "opened" ? "opened" : "blocked",
      reason: r.reason,
      tradeId: r.tradeId,
    };
  }

  // CONFIRM
  const cfg = await resolveRiskConfig(signal.strategyName, signal.symbol);
  const appr = await requestApproval(signal, cfg.approvalTtlMin);
  return { mode, action: "awaiting_approval", reason: appr.reason };
}

export interface ReconcileSummary {
  scanned: number;
  opened: number;
  awaiting: number;
  held: number;
  blocked: number;
}

/**
 * Reconciliation pass for the cron loop. Picks up any PENDING signal that has no
 * trade AND no approval yet (covers a missed webhook, an OFF→AUTO flip, or a
 * restart between gate and decide) and runs it through the decider. Signals that
 * already have an approval are intentionally skipped so the sweep never races a
 * pending human decision.
 */
export async function reconcilePendingSignals(): Promise<ReconcileSummary> {
  const pending = await prisma.signal.findMany({
    where: { status: "PENDING", trades: { none: {} }, approval: { is: null } },
    orderBy: { createdAt: "asc" },
    take: 50,
  });

  const summary: ReconcileSummary = { scanned: pending.length, opened: 0, awaiting: 0, held: 0, blocked: 0 };
  for (const sig of pending) {
    try {
      const d = await decideExecution(sig);
      if (d.action === "opened") summary.opened += 1;
      else if (d.action === "awaiting_approval") summary.awaiting += 1;
      else if (d.action === "held_off") summary.held += 1;
      else summary.blocked += 1;
      console.log(
        `[reconcile] signal=${sig.id} ${sig.symbol}/${sig.timeframe} mode=${d.mode} action=${d.action}` +
          (d.reason ? ` reason="${d.reason}"` : ""),
      );
    } catch (err) {
      console.error(`[reconcile] signal=${sig.id} failed:`, err instanceof Error ? err.message : err);
    }
  }
  return summary;
}
