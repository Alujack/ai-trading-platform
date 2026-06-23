import fs from "node:fs";
import path from "node:path";

/**
 * Telegram credentials, resolved UI-override ► .env. Mirrors the AI-provider
 * key pattern: a value pasted in the dashboard is persisted to a gitignored
 * JSON file and takes precedence over the environment, so the operator never
 * has to edit .env or restart to connect a bot. Secrets are never returned to
 * the client — only presence flags + a token hint.
 */

export interface TelegramOverrides {
  botToken?: string;
  chatId?: string;
  webhookSecret?: string;
  allowedUserIds?: string; // comma-separated
}

// Resolved relative to the API working dir (npm run dev runs from apps/api).
const FILE = path.resolve(process.cwd(), ".telegram-overrides.json");

let cache: TelegramOverrides | null = null;

function load(): TelegramOverrides {
  if (cache) return cache;
  try {
    cache = JSON.parse(fs.readFileSync(FILE, "utf8")) as TelegramOverrides;
  } catch {
    cache = {};
  }
  return cache;
}

function persist(next: TelegramOverrides): void {
  cache = next;
  try {
    fs.writeFileSync(FILE, JSON.stringify(next, null, 2), { mode: 0o600 });
  } catch (err) {
    console.error("[telegram] failed to persist overrides:", err instanceof Error ? err.message : err);
  }
}

const env = (k: string): string => process.env[k] ?? "";

export function getBotToken(): string {
  return load().botToken || env("TELEGRAM_BOT_TOKEN");
}
export function getChatId(): string {
  return load().chatId || env("TELEGRAM_CHAT_ID");
}
export function getWebhookSecret(): string {
  return load().webhookSecret || env("TELEGRAM_WEBHOOK_SECRET");
}
export function getAllowedUserIds(): string[] {
  const raw = load().allowedUserIds || env("TELEGRAM_ALLOWED_USER_IDS");
  return raw.split(",").map((s) => s.trim()).filter(Boolean);
}
export function isConfigured(): boolean {
  return Boolean(getBotToken() && getChatId());
}

/** Merge a partial set of overrides (empty string clears that field). */
export function setOverrides(partial: TelegramOverrides): void {
  const next = { ...load() };
  for (const [k, v] of Object.entries(partial) as [keyof TelegramOverrides, string | undefined][]) {
    if (v == null) continue;
    if (v === "") delete next[k];
    else next[k] = v;
  }
  persist(next);
}

export function clearOverrides(): void {
  persist({});
  try {
    fs.unlinkSync(FILE);
  } catch {
    /* already gone */
  }
}

function source(field: keyof TelegramOverrides, envKey: string): "ui" | "env" | "none" {
  if (load()[field]) return "ui";
  if (env(envKey)) return "env";
  return "none";
}

/** Non-secret status for the dashboard. Never includes raw token/secret. */
export function status() {
  const token = getBotToken();
  return {
    configured: isConfigured(),
    hasToken: Boolean(token),
    tokenHint: token ? `…${token.slice(-6)}` : null,
    chatId: getChatId() || null,
    allowedUserIds: getAllowedUserIds(),
    hasWebhookSecret: Boolean(getWebhookSecret()),
    sources: {
      botToken: source("botToken", "TELEGRAM_BOT_TOKEN"),
      chatId: source("chatId", "TELEGRAM_CHAT_ID"),
      webhookSecret: source("webhookSecret", "TELEGRAM_WEBHOOK_SECRET"),
      allowedUserIds: source("allowedUserIds", "TELEGRAM_ALLOWED_USER_IDS"),
    },
  };
}
