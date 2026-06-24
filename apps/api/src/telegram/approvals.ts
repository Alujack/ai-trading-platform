import type { Signal } from "@prisma/client";
import { resolveRiskConfig } from "../config/resolve";
import { openLiveTrade } from "../execution/liveTrade";
import { openPaperTrade } from "../execution/paperTrading";
import { prisma } from "../lib/prisma";
import { publishEvent } from "../lib/realtime";
import { calculatePositionSize } from "../risk/riskEngine";

function isLiveBroker(): boolean {
  return (process.env.BROKER ?? "paper").trim().toLowerCase() === "exness";
}
import {
  defaultChatId,
  editMessageText,
  isConfigured,
  sendMessage,
} from "./telegram";

/** Escape the few characters Telegram's HTML parse_mode treats specially. */
function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function num(v: { toString(): string } | null | undefined): number {
  return v == null ? 0 : Number(v.toString());
}

function fmt(symbol: string, v: number): string {
  const dp = symbol === "EURUSD" ? 4 : symbol === "BTCUSD" ? 0 : 2;
  return v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function account() {
  return { accountBalance: Number(process.env.PAPER_ACCOUNT_BALANCE ?? "10000") };
}

/** Build the full alert text (plan + AI reasoning) from data the gate produced. */
export async function formatAlert(signal: Signal, ttlMin: number): Promise<string> {
  const cfg = await resolveRiskConfig(signal.strategyName, signal.symbol);
  const { accountBalance } = account();
  const entry = num(signal.entryPrice);
  const stop = num(signal.stopLoss);
  const target = num(signal.takeProfit);
  const risk = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  const rr = risk > 0 ? reward / risk : 0;

  let sizeLine = "—";
  try {
    const sized = calculatePositionSize(accountBalance, cfg.riskPerTradePct, entry, stop);
    sizeLine =
      `${sized.lotSize.toFixed(4)} units  (risk $${sized.riskAmount.toFixed(2)} = ` +
      `${cfg.riskPerTradePct}% of $${accountBalance.toLocaleString()})`;
  } catch {
    /* leave dash */
  }

  const dot = signal.direction === "LONG" ? "🟢" : "🔴";
  const reasoning = (signal.aiReasoning || "").trim().slice(0, 600);

  return [
    `${dot} <b>SIGNAL</b> — ${esc(signal.symbol)} ${esc(signal.timeframe)} · ${signal.direction} · ${esc(signal.strategyName ?? "—")}`,
    `Mode: CONFIRM · expires in ${ttlMin}m`,
    ``,
    `<b>PLAN</b>`,
    `• Entry   ${fmt(signal.symbol, entry)}`,
    `• Stop    ${fmt(signal.symbol, stop)}`,
    `• Target  ${fmt(signal.symbol, target)}`,
    `• R:R     1:${rr.toFixed(2)}`,
    `• Size    ${sizeLine}`,
    ``,
    `<b>WHY</b> (AI score ${signal.confidenceScore}/100)`,
    esc(reasoning) || "—",
  ].join("\n");
}

export interface RequestApprovalResult {
  created: boolean;
  reason?: string;
}

/**
 * Create an Approval(PENDING) for a CONFIRM-mode signal and send the Telegram
 * alert with Approve/Reject buttons. Fail-safe: if Telegram isn't configured or
 * the send fails, the Approval is still recorded so the signal is auditable and
 * the expiry sweep can clean it up; the signal never auto-opens as a fallback.
 */
export async function requestApproval(signal: Signal, ttlMin: number): Promise<RequestApprovalResult> {
  const existing = await prisma.approval.findUnique({ where: { signalId: signal.id } });
  if (existing) return { created: false, reason: "approval_exists" };

  const chatId = defaultChatId() ?? "";
  const expiresAt = new Date(Date.now() + ttlMin * 60_000);

  const approval = await prisma.approval.create({
    data: { signalId: signal.id, chatId, expiresAt },
  });

  if (!isConfigured()) {
    console.warn(`[approvals] Telegram not configured — approval ${approval.id} created without alert`);
    return { created: true, reason: "telegram_not_configured" };
  }

  const text = await formatAlert(signal, ttlMin);
  const messageId = await sendMessage(chatId, text, [
    [
      { text: "✅ Approve", callback_data: `apv:${approval.id}` },
      { text: "❌ Reject", callback_data: `rej:${approval.id}` },
    ],
  ]);

  if (messageId) {
    await prisma.approval.update({ where: { id: approval.id }, data: { messageId } });
  }
  return { created: true };
}

export interface DecisionResult {
  ok: boolean;
  outcome: "approved" | "rejected" | "already_decided" | "expired" | "not_found" | "open_failed";
  message: string;
}

/**
 * Apply an Approve/Reject decision coming back from the Telegram webhook.
 * Idempotent: a second tap on an already-decided/expired approval is a no-op.
 */
export async function applyApprovalDecision(
  approvalId: string,
  approve: boolean,
  decidedBy: string,
): Promise<DecisionResult> {
  const approval = await prisma.approval.findUnique({
    where: { id: approvalId },
    include: { signal: true },
  });
  if (!approval) return { ok: false, outcome: "not_found", message: "Approval not found." };

  if (approval.status !== "PENDING") {
    return { ok: false, outcome: "already_decided", message: `Already ${approval.status.toLowerCase()}.` };
  }
  if (approval.expiresAt.getTime() < Date.now()) {
    await prisma.$transaction([
      prisma.approval.update({ where: { id: approval.id }, data: { status: "EXPIRED" } }),
      prisma.signal.update({ where: { id: approval.signalId }, data: { status: "CANCELLED" } }),
    ]);
    return { ok: false, outcome: "expired", message: "This signal already expired." };
  }

  const stamp = new Date();
  if (!approve) {
    await prisma.$transaction([
      prisma.approval.update({
        where: { id: approval.id },
        data: { status: "REJECTED", decidedBy, decidedAt: stamp },
      }),
      prisma.signal.update({ where: { id: approval.signalId }, data: { status: "CANCELLED" } }),
    ]);
    void publishEvent({ type: "signal", symbol: approval.signal.symbol });
    return { ok: true, outcome: "rejected", message: `❌ Rejected by ${decidedBy}` };
  }

  // Approve → the authoritative risk re-size happens inside the trade opener.
  const opened = isLiveBroker()
    ? await openLiveTrade(approval.signalId)
    : await openPaperTrade(approval.signalId);
  if (opened.status !== "opened") {
    return { ok: false, outcome: "open_failed", message: `Could not open: ${opened.reason ?? "unknown"}` };
  }
  await prisma.approval.update({
    where: { id: approval.id },
    data: { status: "APPROVED", decidedBy, decidedAt: stamp },
  });
  void publishEvent({ type: "trade", symbol: approval.signal.symbol });
  return { ok: true, outcome: "approved", message: `✅ Approved by ${decidedBy} · trade opened` };
}

export interface ExpirySummary {
  expired: number;
}

/**
 * Expire any PENDING approval past its TTL: mark EXPIRED, cancel the signal, and
 * stamp the Telegram message. Run every minute from the scheduler.
 */
export async function expireStaleApprovals(): Promise<ExpirySummary> {
  const stale = await prisma.approval.findMany({
    where: { status: "PENDING", expiresAt: { lt: new Date() } },
    include: { signal: { select: { symbol: true } } },
    take: 100,
  });

  let expired = 0;
  for (const a of stale) {
    await prisma.$transaction([
      prisma.approval.update({ where: { id: a.id }, data: { status: "EXPIRED" } }),
      prisma.signal.update({ where: { id: a.signalId }, data: { status: "CANCELLED" } }),
    ]);
    if (a.messageId) {
      await editMessageText(a.chatId, a.messageId, "⌛ <b>Expired</b> — not taken.");
    }
    void publishEvent({ type: "signal", symbol: a.signal.symbol });
    expired += 1;
  }
  return { expired };
}
