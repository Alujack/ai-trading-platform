/**
 * Morning news brief → Telegram. Part of the agent's "check news first" daily
 * routine: before the session, summarize the high/medium-impact economic events
 * in the next 24h and push them to the user's Telegram chat.
 *
 * Best-effort throughout: a missing AI service, empty calendar, or unconfigured
 * Telegram must never throw into the scheduler. The same NewsEvent rows feed the
 * risk engine's ±news-window block, so this brief and the trade gate stay in sync.
 */

import { prisma } from "../lib/prisma";
import { defaultChatId, isConfigured, sendMessage } from "../telegram/telegram";
import { collectMarketContext } from "./dailyBriefing";

const DAY_MS = 24 * 60 * 60 * 1000;
const AI_SERVICE_URL = process.env.AI_SERVICE_URL ?? "http://localhost:8000";

/** Escape the few characters Telegram's HTML parse_mode treats specially. */
function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

interface BriefEvent {
  title: string;
  impact: string;
  currency: string;
  scheduledAt: Date;
  forecast: string | null;
  previous: string | null;
}

/**
 * Ask the AI service for a short consolidated "what to watch" paragraph. Returns
 * null on any failure so the brief still goes out with just the event list.
 */
async function aiSummary(events: BriefEvent[]): Promise<string | null> {
  try {
    const headlines = events.slice(0, 25).map((e) => ({
      title: e.title,
      source: "economic-calendar",
      publishedAt: e.scheduledAt.toISOString(),
      body: `${e.impact}-impact ${e.currency} event. Forecast: ${e.forecast ?? "n/a"}, Previous: ${e.previous ?? "n/a"}.`,
    }));
    const res = await fetch(`${AI_SERVICE_URL}/analyze/news-summary`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ headlines }),
    });
    if (!res.ok) return null;
    const json = (await res.json()) as { summary?: string };
    return json.summary?.trim() || null;
  } catch {
    return null;
  }
}

/** Morning desk call from the market-context agent: bias + levels + risks per
 *  traded series. Empty string when the AI service has nothing to say. */
async function deskCallSection(): Promise<string[]> {
  const contexts = await collectMarketContext();
  if (contexts.length === 0) return [];
  const lines = ["", "<b>DESK CALL</b>"];
  for (const m of contexts) {
    const dot = m.bias === "Bullish" ? "🟢" : m.bias === "Bearish" ? "🔴" : "⚪";
    lines.push(`${dot} <b>${esc(m.symbol)} ${esc(m.timeframe)}</b> — ${esc(m.bias)}`);
    lines.push(esc(m.summary.slice(0, 300)));
    if (m.keyLevels.length) lines.push(`Levels: ${esc(m.keyLevels.slice(0, 3).join(" · "))}`);
    if (m.risks.length) lines.push(`Risks: ${esc(m.risks.slice(0, 2).join(" · "))}`);
  }
  return lines;
}

/** Build the HTML message body for the next-24h news brief. */
export async function buildNewsBrief(now: Date = new Date()): Promise<string> {
  const events = await prisma.newsEvent.findMany({
    where: {
      scheduledAt: { gt: now, lt: new Date(now.getTime() + DAY_MS) },
      impact: { in: ["HIGH", "MEDIUM"] },
    },
    orderBy: { scheduledAt: "asc" },
    take: 15,
  });

  const desk = await deskCallSection();

  if (events.length === 0) {
    return [
      "📰 <b>NEWS BRIEF</b> — next 24h (UTC)",
      "",
      "No high- or medium-impact events scheduled. Clear to follow your plan — still read the chart before any entry.",
      ...desk,
    ].join("\n");
  }

  const summary = await aiSummary(events);
  const lines = ["📰 <b>NEWS BRIEF</b> — next 24h (UTC)", ""];
  if (summary) lines.push(esc(summary), "");

  lines.push("<b>SCHEDULED</b>");
  for (const e of events) {
    const time = e.scheduledAt.toISOString().slice(11, 16); // HH:MM UTC
    const flag = e.impact === "HIGH" ? "🔴" : "🟠";
    lines.push(`${flag} ${time} <b>${esc(e.currency)}</b> — ${esc(e.title)}`);
  }

  lines.push(...desk);
  lines.push("", "⚠️ Trades auto-block ±30m around high-impact events.");
  return lines.join("\n");
}

/**
 * Build and send the morning news brief to the configured Telegram chat.
 * No-ops (without throwing) when Telegram is not configured.
 */
export async function sendDailyNewsBrief(): Promise<{ sent: boolean; reason?: string }> {
  if (!isConfigured()) return { sent: false, reason: "telegram_not_configured" };
  const chatId = defaultChatId();
  if (!chatId) return { sent: false, reason: "no_chat_id" };

  const text = await buildNewsBrief();
  const id = await sendMessage(chatId, text);
  return id != null ? { sent: true } : { sent: false, reason: "send_failed" };
}
