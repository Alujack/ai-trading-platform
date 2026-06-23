/**
 * Telegram bot helper CLI. Run with tsx (see npm scripts), e.g.
 *
 *   npm run telegram -- ids                  # discover your chat id + user id
 *   npm run telegram -- set https://abc.trycloudflare.com
 *   npm run telegram -- info
 *   npm run telegram -- test
 *   npm run telegram -- delete
 *
 * Reads TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET / TELEGRAM_CHAT_ID from the
 * environment (.env). No secrets are printed.
 */
import "dotenv/config";

const TOKEN = process.env.TELEGRAM_BOT_TOKEN ?? "";
const SECRET = process.env.TELEGRAM_WEBHOOK_SECRET ?? "";
const CHAT_ID = process.env.TELEGRAM_CHAT_ID ?? "";
const WEBHOOK_PATH = "/api/internal/telegram/webhook";

function die(msg: string): never {
  console.error(`✗ ${msg}`);
  process.exit(1);
}

if (!TOKEN) die("TELEGRAM_BOT_TOKEN is not set in .env — create your bot via @BotFather first.");

const API = `https://api.telegram.org/bot${TOKEN}`;

async function call(method: string, body?: Record<string, unknown>): Promise<any> {
  const res = await fetch(`${API}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = (await res.json()) as { ok: boolean; result?: unknown; description?: string };
  if (!json.ok) die(`${method} failed: ${json.description ?? res.status}`);
  return json.result;
}

async function ids(): Promise<void> {
  // getUpdates conflicts with an active webhook — clear it first.
  await call("deleteWebhook");
  const updates = (await call("getUpdates")) as any[];
  if (!updates.length) {
    console.log(
      "No updates yet. Open Telegram, send any message (e.g. /start) to your bot, then re-run this.\n" +
        "(Webhook was temporarily removed to read updates — run `set` again afterward.)",
    );
    return;
  }
  const seen = new Map<string, string>();
  for (const u of updates) {
    const m = u.message ?? u.callback_query?.message;
    const from = u.message?.from ?? u.callback_query?.from;
    if (from) seen.set(String(from.id), from.username ? `@${from.username}` : from.first_name ?? "?");
    if (m?.chat) seen.set(`chat:${m.chat.id}`, m.chat.type ?? "chat");
  }
  console.log("Discovered IDs (set these in .env):\n");
  for (const [id, label] of seen) {
    if (id.startsWith("chat:")) console.log(`  TELEGRAM_CHAT_ID=${id.slice(5)}   (${label})`);
    else console.log(`  TELEGRAM_ALLOWED_USER_IDS=${id}   (user ${label})`);
  }
  console.log("\nThen run `set <publicUrl>` to (re)register the webhook.");
}

async function set(base: string | undefined): Promise<void> {
  if (!base) die("usage: set <publicUrl>   e.g. set https://abc.trycloudflare.com");
  if (!SECRET) die("TELEGRAM_WEBHOOK_SECRET is not set — generate one: openssl rand -hex 24");
  const url = `${base.replace(/\/$/, "")}${WEBHOOK_PATH}`;
  await call("setWebhook", {
    url,
    secret_token: SECRET,
    allowed_updates: ["message", "callback_query"],
    drop_pending_updates: true,
  });
  console.log(`✓ Webhook registered → ${url}`);
}

async function info(): Promise<void> {
  const r = (await call("getWebhookInfo")) as {
    url: string;
    pending_update_count: number;
    last_error_message?: string;
  };
  console.log(`url:     ${r.url || "(none)"}`);
  console.log(`pending: ${r.pending_update_count}`);
  if (r.last_error_message) console.log(`lastErr: ${r.last_error_message}`);
}

async function test(): Promise<void> {
  if (!CHAT_ID) die("TELEGRAM_CHAT_ID is not set — run `ids` to discover it.");
  await call("sendMessage", { chat_id: CHAT_ID, text: "✅ AI Trading bot connected — test message." });
  console.log(`✓ Sent test message to chat ${CHAT_ID}`);
}

const [cmd, arg] = process.argv.slice(2);
const run =
  cmd === "ids" ? ids() :
  cmd === "set" ? set(arg) :
  cmd === "info" ? info() :
  cmd === "test" ? test() :
  cmd === "delete" ? call("deleteWebhook").then(() => console.log("✓ Webhook deleted")) :
  Promise.resolve(
    console.log("commands: ids | set <publicUrl> | info | test | delete"),
  );

run.catch((e) => die(e instanceof Error ? e.message : String(e)));
