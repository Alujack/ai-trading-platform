import cron, { type ScheduledTask } from "node-cron";
import { reconcilePendingSignals } from "./executionPolicy";
import { monitorLiveTrades } from "./liveTrade";
import { runScalpManagementTick } from "./scalpManager";
import { monitorOpenTrades, runWeeklyJournalReview } from "./paperTrading";
import { runDailyBriefing } from "./dailyBriefing";
import { sendDailyNewsBrief } from "./newsBrief";
import { expireStaleApprovals } from "../telegram/approvals";

function isLiveBroker(): boolean {
  return (process.env.BROKER ?? "paper").trim().toLowerCase() === "exness";
}

/** The fast scalp-management loop is opt-in: it only runs live and only when
 *  explicitly enabled, so the 5-min reconciler stays the default everywhere else. */
function scalpManagerEnabled(): boolean {
  return (process.env.ENABLE_SCALP_MANAGER ?? "false").trim().toLowerCase() === "true";
}

/** The morning Telegram news brief is on by default; set ENABLE_NEWS_BRIEF=false to mute it. */
function newsBriefEnabled(): boolean {
  return (process.env.ENABLE_NEWS_BRIEF ?? "true").trim().toLowerCase() !== "false";
}

const PAPER_CRON = "*/5 * * * *";
const SCALP_CRON = "*/15 * * * * *"; // every 15s — scalps need active management, not the 5-min loop
const WEEKLY_CRON = "0 0 * * 0"; // Sunday 00:00 UTC
const DAILY_CRON = "0 6 * * *"; // 06:00 UTC — morning briefing before the session
const EXPIRY_CRON = "* * * * *"; // every minute — approvals are perishable

let paperTask: ScheduledTask | null = null;
let scalpTask: ScheduledTask | null = null;
let weeklyTask: ScheduledTask | null = null;
let dailyTask: ScheduledTask | null = null;
let expiryTask: ScheduledTask | null = null;
let paperRunning = false;
let scalpRunning = false;
let weeklyRunning = false;
let dailyRunning = false;
let expiryRunning = false;

async function runPaperTick(): Promise<void> {
  if (paperRunning) {
    console.log(`[paperCron] ${new Date().toISOString()} prev_run_in_progress, skipping`);
    return;
  }
  paperRunning = true;
  const startedAt = new Date().toISOString();
  console.log(`[paperCron] ${startedAt} run_start`);
  try {
    const rec = await reconcilePendingSignals();
    console.log(
      `[paperCron] ${new Date().toISOString()} reconcile scanned=${rec.scanned} opened=${rec.opened} awaiting=${rec.awaiting} held=${rec.held} blocked=${rec.blocked}`,
    );
    if (isLiveBroker()) {
      const mon = await monitorLiveTrades();
      console.log(
        `[paperCron] ${new Date().toISOString()} live_monitor inspected=${mon.inspected} closed=${mon.closed} unchanged=${mon.unchanged}`,
      );
    } else {
      const mon = await monitorOpenTrades();
      console.log(
        `[paperCron] ${new Date().toISOString()} monitor inspected=${mon.inspected} closed=${mon.closed} unchanged=${mon.unchanged} no_price=${mon.noPrice}`,
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[paperCron] ${new Date().toISOString()} error="${msg}"`);
  } finally {
    paperRunning = false;
    console.log(`[paperCron] ${new Date().toISOString()} run_end`);
  }
}

async function runScalpTick(): Promise<void> {
  if (scalpRunning) return; // a prior tick (e.g. one that closed + journaled) is still running
  scalpRunning = true;
  try {
    const r = await runScalpManagementTick();
    if (r.closed > 0) {
      console.log(
        `[scalpManager] ${new Date().toISOString()} managed=${r.managed} closed=${r.closed} held=${r.held} gone=${r.gone}`,
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[scalpManager] ${new Date().toISOString()} error="${msg}"`);
  } finally {
    scalpRunning = false;
  }
}

async function runWeeklyTick(): Promise<void> {
  if (weeklyRunning) {
    console.log(`[weeklyReviewCron] ${new Date().toISOString()} prev_run_in_progress, skipping`);
    return;
  }
  weeklyRunning = true;
  const startedAt = new Date().toISOString();
  console.log(`[weeklyReviewCron] ${startedAt} run_start`);
  try {
    const r = await runWeeklyJournalReview();
    console.log(
      `[weeklyReviewCron] ${new Date().toISOString()} status=${r.status} trades=${r.tradeCount ?? 0}${r.reason ? ` reason="${r.reason}"` : ""}`,
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[weeklyReviewCron] ${new Date().toISOString()} error="${msg}"`);
  } finally {
    weeklyRunning = false;
  }
}

async function runDailyTick(notify: boolean = true): Promise<void> {
  if (dailyRunning) {
    console.log(`[dailyBriefingCron] ${new Date().toISOString()} prev_run_in_progress, skipping`);
    return;
  }
  dailyRunning = true;
  try {
    await runDailyBriefing();
    // Push the morning news brief to Telegram on the scheduled run only — not on
    // the startup-once pass, so restarts don't spam the chat.
    if (notify && newsBriefEnabled()) {
      const r = await sendDailyNewsBrief();
      console.log(
        `[newsBrief] ${new Date().toISOString()} sent=${r.sent}${r.reason ? ` reason=${r.reason}` : ""}`,
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[dailyBriefingCron] ${new Date().toISOString()} error="${msg}"`);
  } finally {
    dailyRunning = false;
  }
}

async function runExpiryTick(): Promise<void> {
  if (expiryRunning) return;
  expiryRunning = true;
  try {
    const r = await expireStaleApprovals();
    if (r.expired > 0) {
      console.log(`[approvalExpiry] ${new Date().toISOString()} expired=${r.expired}`);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[approvalExpiry] ${new Date().toISOString()} error="${msg}"`);
  } finally {
    expiryRunning = false;
  }
}

export function startPaperTradingScheduler(): void {
  if (paperTask) return;
  console.log(`[paperCron] scheduled "${PAPER_CRON}"`);
  paperTask = cron.schedule(PAPER_CRON, () => {
    void runPaperTick();
  });
  if (!expiryTask) {
    console.log(`[approvalExpiry] scheduled "${EXPIRY_CRON}"`);
    expiryTask = cron.schedule(EXPIRY_CRON, () => {
      void runExpiryTick();
    });
  }
  if (!scalpTask && isLiveBroker() && scalpManagerEnabled()) {
    console.log(`[scalpManager] scheduled "${SCALP_CRON}"`);
    scalpTask = cron.schedule(SCALP_CRON, () => {
      void runScalpTick();
    });
  }
}

export function startWeeklyReviewScheduler(): void {
  if (weeklyTask) return;
  console.log(`[weeklyReviewCron] scheduled "${WEEKLY_CRON}"`);
  weeklyTask = cron.schedule(WEEKLY_CRON, () => {
    void runWeeklyTick();
  });
}

export function startDailyBriefingScheduler(): void {
  if (dailyTask) return;
  console.log(`[dailyBriefingCron] scheduled "${DAILY_CRON}"`);
  dailyTask = cron.schedule(DAILY_CRON, () => {
    void runDailyTick();
  });
}

export function stopExecutionSchedulers(): void {
  if (dailyTask) {
    dailyTask.stop();
    dailyTask = null;
    console.log("[dailyBriefingCron] stopped");
  }
  if (paperTask) {
    paperTask.stop();
    paperTask = null;
    console.log("[paperCron] stopped");
  }
  if (scalpTask) {
    scalpTask.stop();
    scalpTask = null;
    console.log("[scalpManager] stopped");
  }
  if (weeklyTask) {
    weeklyTask.stop();
    weeklyTask = null;
    console.log("[weeklyReviewCron] stopped");
  }
  if (expiryTask) {
    expiryTask.stop();
    expiryTask = null;
    console.log("[approvalExpiry] stopped");
  }
}

export async function runPaperTradingOnce(): Promise<void> {
  await runPaperTick();
}

export async function runScalpManagementOnce(): Promise<void> {
  await runScalpTick();
}

export async function runWeeklyReviewOnce(): Promise<void> {
  await runWeeklyTick();
}

export async function runDailyBriefingOnce(): Promise<void> {
  await runDailyTick(false); // recompute the briefing on startup, but don't ping Telegram
}

/** On-demand: build and push the news brief to Telegram now (e.g. a manual route). */
export async function sendNewsBriefOnce(): Promise<{ sent: boolean; reason?: string }> {
  return sendDailyNewsBrief();
}
