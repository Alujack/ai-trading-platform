/**
 * Data-freshness guard (docs/plans/10, Phase 0) — the alerting half.
 *
 * The Python strategy runner already refuses to evaluate a stale series
 * (services/data/strategy_runner.py); this daily check tells the operator THAT
 * it is refusing, via Telegram. The July 2026 ingestion outage ran unnoticed
 * for weeks precisely because the pipeline went quiet without complaint.
 *
 * Best-effort throughout: an unconfigured Telegram or an empty Candle table
 * must never throw into the scheduler.
 */

import { prisma } from "../lib/prisma";
import { defaultChatId, isConfigured, sendMessage } from "../telegram/telegram";

// Mirrors _STALE_AGE_LIMIT_S in services/data/strategy_runner.py: 2× the
// timeframe for intraday, 3 days for daily so a weekend can't false-alarm it.
const STALE_LIMIT_MS: Record<string, number> = {
  "1min": 2 * 60_000,
  "5min": 2 * 5 * 60_000,
  "15min": 2 * 15 * 60_000,
  "60min": 2 * 3_600_000,
  daily: 3 * 86_400_000,
};

// FX/metals venues are closed Fri 22:00 → Sun 22:00 UTC, so intraday series
// legitimately freeze there. Crypto trades through and is exempt from the
// weekend adjustment (mirrors _WEEKEND_OPEN_SYMBOLS in services/data/fetcher.py).
const WEEKEND_OPEN_SYMBOLS = new Set(["BTCUSD"]);

export interface FreshnessIssue {
  symbol: string;
  timeframe: string;
  newestBar: Date | null;
  ageMinutes: number | null;
}

function limitFor(timeframe: string): number {
  return STALE_LIMIT_MS[timeframe] ?? 2 * 3_600_000;
}

/** During the weekend gap, measure staleness from the Friday 22:00 UTC close
 *  instead of now, so a healthy-but-closed market doesn't page anyone. */
function effectiveNow(now: Date, symbol: string): Date {
  if (WEEKEND_OPEN_SYMBOLS.has(symbol)) return now;
  const day = now.getUTCDay(); // Sun=0 … Sat=6
  const inGap =
    (day === 5 && now.getUTCHours() >= 22) ||
    day === 6 ||
    (day === 0 && now.getUTCHours() < 22);
  if (!inGap) return now;
  const fridayClose = new Date(now);
  const daysBackToFriday = day === 5 ? 0 : day === 6 ? 1 : 2;
  fridayClose.setUTCDate(fridayClose.getUTCDate() - daysBackToFriday);
  fridayClose.setUTCHours(22, 0, 0, 0);
  return fridayClose;
}

/** (symbol, timeframe) pairs the desk actually trades: the union of every
 *  enabled strategy's params scoping. A strategy without explicit scoping
 *  widens the check to every distinct pair present in the Candle table. */
export async function tradedPairs(): Promise<Array<{ symbol: string; timeframe: string }>> {
  const strategies = await prisma.strategy.findMany({ where: { enabled: true } });
  if (strategies.length === 0) return [];
  const pairs = new Map<string, { symbol: string; timeframe: string }>();
  let needFallback = false;
  for (const s of strategies) {
    const p = (s.params ?? {}) as { symbols?: unknown; timeframes?: unknown };
    const symbols = Array.isArray(p.symbols)
      ? p.symbols.filter((x): x is string => typeof x === "string" && x.trim() !== "")
      : [];
    const timeframes = Array.isArray(p.timeframes)
      ? p.timeframes.filter((x): x is string => typeof x === "string" && x.trim() !== "")
      : [];
    if (symbols.length === 0 || timeframes.length === 0) {
      needFallback = true;
      continue;
    }
    for (const sym of symbols) {
      for (const tf of timeframes) {
        pairs.set(`${sym}|${tf}`, { symbol: sym, timeframe: tf });
      }
    }
  }
  if (needFallback) {
    const distinct = await prisma.candle.findMany({
      distinct: ["symbol", "timeframe"],
      select: { symbol: true, timeframe: true },
    });
    for (const d of distinct) pairs.set(`${d.symbol}|${d.timeframe}`, d);
  }
  return [...pairs.values()];
}

/** Every traded series whose newest bar is older than its staleness limit. */
export async function checkDataFreshness(now: Date = new Date()): Promise<FreshnessIssue[]> {
  const issues: FreshnessIssue[] = [];
  for (const { symbol, timeframe } of await tradedPairs()) {
    const newest = await prisma.candle.findFirst({
      where: { symbol, timeframe },
      orderBy: { timestamp: "desc" },
      select: { timestamp: true },
    });
    if (!newest) {
      issues.push({ symbol, timeframe, newestBar: null, ageMinutes: null });
      continue;
    }
    const ageMs = effectiveNow(now, symbol).getTime() - newest.timestamp.getTime();
    if (ageMs > limitFor(timeframe)) {
      issues.push({
        symbol,
        timeframe,
        newestBar: newest.timestamp,
        ageMinutes: Math.round(ageMs / 60_000),
      });
    }
  }
  return issues;
}

/**
 * Daily staleness alert → Telegram. No-ops (without throwing) when everything
 * is fresh or Telegram is not configured.
 */
export async function sendDataFreshnessAlert(): Promise<{
  sent: boolean;
  stale: number;
  reason?: string;
}> {
  const issues = await checkDataFreshness();
  if (issues.length === 0) return { sent: false, stale: 0, reason: "all_fresh" };
  if (!isConfigured()) return { sent: false, stale: issues.length, reason: "telegram_not_configured" };
  const chatId = defaultChatId();
  if (!chatId) return { sent: false, stale: issues.length, reason: "no_chat_id" };

  const lines = ["🩸 <b>DATA STALE</b> — strategy scans are blocked on:", ""];
  for (const i of issues) {
    const age =
      i.ageMinutes == null
        ? "no candles at all"
        : i.ageMinutes >= 120
          ? `${Math.round(i.ageMinutes / 60)}h old`
          : `${i.ageMinutes}m old`;
    lines.push(`• <b>${i.symbol}</b> ${i.timeframe} — newest bar ${age}`);
  }
  lines.push("", "Check the data worker / provider quota, then re-run ingestion.");

  const id = await sendMessage(chatId, lines.join("\n"));
  return id != null
    ? { sent: true, stale: issues.length }
    : { sent: false, stale: issues.length, reason: "send_failed" };
}
