import { Router } from "express";
import type { ExecutionMode } from "@prisma/client";
import { z } from "zod";
import { SYMBOL_CURRENCIES } from "../config/defaults";
import { getExecutionMap } from "../config/resolve";
import { armSystem, setKillSwitch, writeExecutionMode } from "../config/store";
import { isBreakerTrippedToday } from "../execution/executionPolicy";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";
import { applyApprovalDecision } from "../telegram/approvals";
import {
  clearOverrides,
  getChatId,
  isConfigured,
  setOverrides,
  status as telegramStatus,
} from "../telegram/config";
import {
  allowedUserIds,
  answerCallbackQuery,
  editMessageText,
  getWebhookInfo,
  registerWebhook,
  sendMessage,
  webhookSecret,
} from "../telegram/telegram";

const router = Router();

const KNOWN_SYMBOLS = new Set(Object.keys(SYMBOL_CURRENCIES));

function num(v: { toString(): string } | null | undefined): number {
  return v == null ? 0 : Number(v.toString());
}

function authorized(userId: string | undefined): boolean {
  const allow = allowedUserIds();
  if (allow.length === 0) {
    console.warn("[telegram] TELEGRAM_ALLOWED_USER_IDS empty — allowing all (dev mode)");
    return true;
  }
  return userId != null && allow.includes(userId);
}

// ---- command handlers ------------------------------------------------------

async function cmdStatus(): Promise<string> {
  const base = Number(process.env.PAPER_ACCOUNT_BALANCE ?? "10000");
  const [realized, open, map, breaker] = await Promise.all([
    prisma.trade.aggregate({ where: { status: "CLOSED" }, _sum: { profitLoss: true } }),
    prisma.trade.count({ where: { status: "OPEN" } }),
    getExecutionMap(),
    isBreakerTrippedToday(),
  ]);
  const startOfDay = new Date();
  startOfDay.setUTCHours(0, 0, 0, 0);
  const today = await prisma.trade.aggregate({
    where: { status: "CLOSED", closedAt: { gte: startOfDay } },
    _sum: { profitLoss: true },
  });
  const equity = base + num(realized._sum.profitLoss);
  return [
    `📊 <b>STATUS</b>`,
    `Equity     $${equity.toFixed(2)}`,
    `Day P&L    $${num(today._sum.profitLoss).toFixed(2)}`,
    `Open       ${open}`,
    `Mode       ${map.global} (global)`,
    `Breaker    ${breaker.tripped ? `🔴 ${breaker.reason}` : "🟢 clear"}`,
  ].join("\n");
}

async function cmdPositions(): Promise<string> {
  const open = await prisma.trade.findMany({
    where: { status: "OPEN" },
    include: { signal: { select: { symbol: true, direction: true } } },
    orderBy: { openedAt: "desc" },
    take: 20,
  });
  if (open.length === 0) return "No open positions.";
  const lines = open.map(
    (t) =>
      `• ${t.signal.symbol} ${t.signal.direction} ${num(t.positionSize).toFixed(4)} @ ${num(t.entryPrice)} (risk $${num(t.riskAmount).toFixed(0)})`,
  );
  return [`📈 <b>OPEN POSITIONS</b> (${open.length})`, ...lines].join("\n");
}

async function cmdPending(): Promise<string> {
  const pending = await prisma.approval.findMany({
    where: { status: "PENDING" },
    include: { signal: { select: { symbol: true, direction: true, strategyName: true } } },
    orderBy: { createdAt: "asc" },
    take: 20,
  });
  if (pending.length === 0) return "No approvals awaiting a decision.";
  const lines = pending.map((a) => {
    const mins = Math.max(0, Math.round((a.expiresAt.getTime() - Date.now()) / 60_000));
    return `• ${a.signal.symbol} ${a.signal.direction} ${a.signal.strategyName ?? ""} — expires ${mins}m`;
  });
  return [`⏳ <b>AWAITING APPROVAL</b> (${pending.length})`, ...lines].join("\n");
}

async function cmdMode(actor: string, args: string[]): Promise<string> {
  const modeArg = (args[0] ?? "").toUpperCase();
  if (!["AUTO", "CONFIRM", "OFF"].includes(modeArg)) {
    return "Usage: /mode auto|confirm|off [SYMBOL|strategy]";
  }
  const mode = modeArg as ExecutionMode;
  const target = args[1];
  if (!target) {
    await writeExecutionMode(actor, "GLOBAL", "", mode);
    return `Global mode → <b>${mode}</b>`;
  }
  if (KNOWN_SYMBOLS.has(target.toUpperCase())) {
    await writeExecutionMode(actor, "SYMBOL", target.toUpperCase(), mode);
    return `${target.toUpperCase()} mode → <b>${mode}</b>`;
  }
  await writeExecutionMode(actor, "STRATEGY", target, mode);
  return `Strategy ${target} mode → <b>${mode}</b>`;
}

async function handleCommand(actor: string, text: string): Promise<string> {
  const [cmd, ...args] = text.trim().split(/\s+/);
  switch (cmd.toLowerCase()) {
    case "/status":
      return cmdStatus();
    case "/positions":
      return cmdPositions();
    case "/pending":
      return cmdPending();
    case "/mode":
      return cmdMode(actor, args);
    case "/kill":
      await setKillSwitch(actor);
      return "🛑 <b>KILL</b> — global mode OFF. No new trades will open.";
    case "/arm":
      await armSystem(actor);
      return "✅ <b>ARMED</b> — global mode CONFIRM.";
    case "/start":
    case "/help":
      return [
        "Commands:",
        "/status — equity, open, mode, breaker",
        "/positions — open trades",
        "/pending — approvals awaiting decision",
        "/mode auto|confirm|off [SYMBOL|strategy]",
        "/kill — global OFF (panic)",
        "/arm — clear OFF",
      ].join("\n");
    default:
      return "Unknown command. Send /help.";
  }
}

// ---- dashboard config surface ---------------------------------------------
// Lets the operator paste the bot token / chat id / allowlist from the UI
// instead of editing .env. Secrets are stored in a gitignored file and never
// returned — only presence flags + a token hint.

const saveSchema = z.object({
  botToken: z.string().max(200).optional(),
  chatId: z.string().max(64).optional(),
  // Telegram only accepts A-Z a-z 0-9 _ - in the webhook secret token. Reject
  // anything else up front so a bad value can't silently 401 every callback.
  // The empty-string case is allowed so the field can be cleared.
  webhookSecret: z
    .string()
    .max(256)
    .regex(/^[A-Za-z0-9_-]*$/, "Webhook secret may only contain letters, numbers, _ and -")
    .optional(),
  allowedUserIds: z.string().max(200).optional(),
});
const registerSchema = z.object({ publicUrl: z.string().url().max(300) });

router.get(
  "/telegram",
  asyncHandler(async (_req, res) => {
    const base = telegramStatus();
    const webhook = base.hasToken ? await getWebhookInfo() : null;
    res.json({ ...base, webhook });
  }),
);

router.put(
  "/telegram",
  validate(saveSchema, "body"),
  asyncHandler(async (req, res) => {
    setOverrides(req.body as Record<string, string | undefined>);
    res.json({ ok: true, ...telegramStatus() });
  }),
);

router.delete(
  "/telegram",
  asyncHandler(async (_req, res) => {
    clearOverrides();
    res.json({ ok: true, ...telegramStatus() });
  }),
);

// Send a test message to the configured chat — proves token + chat id work.
router.post(
  "/telegram/test",
  asyncHandler(async (_req, res) => {
    if (!isConfigured()) {
      res.status(400).json({ ok: false, error: "Set a bot token and chat id first." });
      return;
    }
    const messageId = await sendMessage(getChatId(), "✅ AI Trading bot connected — test message.");
    if (!messageId) {
      res.status(502).json({ ok: false, error: "Telegram rejected the send — check the token and chat id." });
      return;
    }
    res.json({ ok: true, detail: "Test message sent." });
  }),
);

// Register the inbound webhook at a public URL (e.g. a cloudflared tunnel).
router.post(
  "/telegram/webhook",
  validate(registerSchema, "body"),
  asyncHandler(async (req, res) => {
    const { publicUrl } = req.body as { publicUrl: string };
    const result = await registerWebhook(publicUrl);
    if (!result.ok) {
      res.status(502).json({ ok: false, error: result.error, url: result.url });
      return;
    }
    res.json({ ok: true, url: result.url });
  }),
);

// ---- webhook ---------------------------------------------------------------

interface TgUser {
  id?: number;
}
interface TgChat {
  id?: number;
}
interface TgMessage {
  message_id?: number;
  chat?: TgChat;
  from?: TgUser;
  text?: string;
}
interface TgCallback {
  id?: string;
  from?: TgUser;
  message?: TgMessage;
  data?: string;
}
interface TgUpdate {
  message?: TgMessage;
  callback_query?: TgCallback;
}

/**
 * Inbound Telegram webhook: verifies the secret-token header, authorizes the
 * sender against the allowlist, then routes Approve/Reject callbacks and text
 * commands. Always responds 200 fast; Telegram edits are fire-and-forget.
 */
router.post(
  "/internal/telegram/webhook",
  asyncHandler(async (req, res) => {
    const secret = webhookSecret();
    if (secret && req.header("X-Telegram-Bot-Api-Secret-Token") !== secret) {
      res.status(401).json({ error: "bad secret" });
      return;
    }

    const update = (req.body ?? {}) as TgUpdate;
    // Acknowledge immediately; do the work without blocking the response.
    res.status(200).json({ ok: true });

    void (async () => {
      try {
        if (update.callback_query) {
          const cq = update.callback_query;
          const userId = cq.from?.id != null ? String(cq.from.id) : undefined;
          const chatId = cq.message?.chat?.id != null ? String(cq.message.chat.id) : undefined;
          const messageId = cq.message?.message_id != null ? String(cq.message.message_id) : undefined;

          if (!authorized(userId)) {
            if (cq.id) await answerCallbackQuery(cq.id, "Not authorized.");
            return;
          }
          const [kind, approvalId] = (cq.data ?? "").split(":");
          if ((kind !== "apv" && kind !== "rej") || !approvalId) {
            if (cq.id) await answerCallbackQuery(cq.id, "Unrecognized action.");
            return;
          }
          const decidedBy = `telegram:${userId}`;
          const result = await applyApprovalDecision(approvalId, kind === "apv", decidedBy);
          if (cq.id) await answerCallbackQuery(cq.id, result.message);
          if (chatId && messageId) {
            const stamp = new Date().toISOString().replace("T", " ").slice(0, 16);
            await editMessageText(chatId, messageId, `${result.message} · ${stamp} UTC`);
          }
          return;
        }

        if (update.message?.text) {
          const msg = update.message;
          const userId = msg.from?.id != null ? String(msg.from.id) : undefined;
          const chatId = msg.chat?.id != null ? String(msg.chat.id) : undefined;
          if (!chatId) return;
          if (!authorized(userId)) {
            await sendMessage(chatId, "Not authorized.");
            return;
          }
          const reply = await handleCommand(`telegram:${userId}`, msg.text ?? "");
          await sendMessage(chatId, reply);
        }
      } catch (err) {
        console.error("[telegram] webhook handler error:", err instanceof Error ? err.message : err);
      }
    })();
  }),
);

export default router;
