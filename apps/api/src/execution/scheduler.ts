import cron, { type ScheduledTask } from "node-cron";
import { monitorOpenTrades, runWeeklyJournalReview, sweepPendingSignals } from "./paperTrading";

const PAPER_CRON = "*/5 * * * *";
const WEEKLY_CRON = "0 0 * * 0"; // Sunday 00:00 UTC

let paperTask: ScheduledTask | null = null;
let weeklyTask: ScheduledTask | null = null;
let paperRunning = false;
let weeklyRunning = false;

async function runPaperTick(): Promise<void> {
  if (paperRunning) {
    console.log(`[paperCron] ${new Date().toISOString()} prev_run_in_progress, skipping`);
    return;
  }
  paperRunning = true;
  const startedAt = new Date().toISOString();
  console.log(`[paperCron] ${startedAt} run_start`);
  try {
    const swept = await sweepPendingSignals();
    console.log(
      `[paperCron] ${new Date().toISOString()} sweep scanned=${swept.scanned} opened=${swept.opened} skipped=${swept.skipped}`,
    );
    const mon = await monitorOpenTrades();
    console.log(
      `[paperCron] ${new Date().toISOString()} monitor inspected=${mon.inspected} closed=${mon.closed} unchanged=${mon.unchanged} no_price=${mon.noPrice}`,
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[paperCron] ${new Date().toISOString()} error="${msg}"`);
  } finally {
    paperRunning = false;
    console.log(`[paperCron] ${new Date().toISOString()} run_end`);
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

export function startPaperTradingScheduler(): void {
  if (paperTask) return;
  console.log(`[paperCron] scheduled "${PAPER_CRON}"`);
  paperTask = cron.schedule(PAPER_CRON, () => {
    void runPaperTick();
  });
}

export function startWeeklyReviewScheduler(): void {
  if (weeklyTask) return;
  console.log(`[weeklyReviewCron] scheduled "${WEEKLY_CRON}"`);
  weeklyTask = cron.schedule(WEEKLY_CRON, () => {
    void runWeeklyTick();
  });
}

export function stopExecutionSchedulers(): void {
  if (paperTask) {
    paperTask.stop();
    paperTask = null;
    console.log("[paperCron] stopped");
  }
  if (weeklyTask) {
    weeklyTask.stop();
    weeklyTask = null;
    console.log("[weeklyReviewCron] stopped");
  }
}

export async function runPaperTradingOnce(): Promise<void> {
  await runPaperTick();
}

export async function runWeeklyReviewOnce(): Promise<void> {
  await runWeeklyTick();
}
