/**
 * Thin Telegram Bot API client. All network calls are best-effort and never
 * throw into the caller — a Telegram outage must fail closed (signal stays
 * PENDING), never crash the execution path. Secrets live in .env only.
 */

import {
  getAllowedUserIds,
  getBotToken,
  getChatId,
  getWebhookSecret,
  isConfigured as cfgConfigured,
} from "./config";

const API_BASE = "https://api.telegram.org";

export interface InlineButton {
  text: string;
  callback_data: string;
}

function token(): string | null {
  return getBotToken() || null;
}

export function defaultChatId(): string | null {
  return getChatId() || null;
}

export function isConfigured(): boolean {
  return cfgConfigured();
}

/** Allowlisted Telegram user ids permitted to approve/command. */
export function allowedUserIds(): string[] {
  return getAllowedUserIds();
}

export function webhookSecret(): string | null {
  return getWebhookSecret() || null;
}

async function call<T = unknown>(method: string, payload: Record<string, unknown>): Promise<T | null> {
  const t = token();
  if (!t) {
    console.warn(`[telegram] ${method} skipped — TELEGRAM_BOT_TOKEN not set`);
    return null;
  }
  try {
    const res = await fetch(`${API_BASE}/bot${t}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const json = (await res.json()) as { ok: boolean; result?: T; description?: string };
    if (!json.ok) {
      console.error(`[telegram] ${method} failed: ${json.description ?? res.status}`);
      return null;
    }
    return json.result ?? null;
  } catch (err) {
    console.error(`[telegram] ${method} error:`, err instanceof Error ? err.message : err);
    return null;
  }
}

/** Send a message with an optional inline keyboard. Returns the message_id. */
export async function sendMessage(
  chatId: string,
  text: string,
  buttons?: InlineButton[][],
): Promise<string | null> {
  const payload: Record<string, unknown> = {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
  };
  if (buttons) payload.reply_markup = { inline_keyboard: buttons };
  const result = await call<{ message_id: number }>("sendMessage", payload);
  return result ? String(result.message_id) : null;
}

/** Edit a message in place and drop its buttons (used to stamp an outcome). */
export async function editMessageText(chatId: string, messageId: string, text: string): Promise<void> {
  await call("editMessageText", {
    chat_id: chatId,
    message_id: Number(messageId),
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
    reply_markup: { inline_keyboard: [] },
  });
}

/** Clear the user's loading spinner after a button tap. */
export async function answerCallbackQuery(callbackQueryId: string, text?: string): Promise<void> {
  await call("answerCallbackQuery", { callback_query_id: callbackQueryId, text: text ?? "" });
}

export const WEBHOOK_PATH = "/api/internal/telegram/webhook";

/** Register the inbound webhook at `<publicUrl>/api/internal/telegram/webhook`. */
export async function registerWebhook(
  publicUrl: string,
): Promise<{ ok: boolean; url: string; error?: string }> {
  const t = token();
  const url = `${publicUrl.replace(/\/$/, "")}${WEBHOOK_PATH}`;
  if (!t) return { ok: false, url, error: "bot token not set" };
  try {
    const res = await fetch(`${API_BASE}/bot${t}/setWebhook`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        url,
        secret_token: getWebhookSecret() || undefined,
        allowed_updates: ["message", "callback_query"],
        drop_pending_updates: true,
      }),
    });
    const json = (await res.json()) as { ok: boolean; description?: string };
    if (!json.ok) return { ok: false, url, error: json.description ?? `http ${res.status}` };
    return { ok: true, url };
  } catch (err) {
    return { ok: false, url, error: err instanceof Error ? err.message : String(err) };
  }
}

export async function getWebhookInfo(): Promise<{ url: string; pending: number; lastError?: string } | null> {
  const r = await call<{ url: string; pending_update_count: number; last_error_message?: string }>(
    "getWebhookInfo",
    {},
  );
  if (!r) return null;
  return { url: r.url, pending: r.pending_update_count, lastError: r.last_error_message };
}
