import cron, { type ScheduledTask } from "node-cron";
import { generateSignal } from "./signalGenerator";

const DEFAULT_SYMBOLS = ["XAUUSD", "EURUSD", "BTCUSD"];
const DEFAULT_TIMEFRAMES = ["15min"];
const CRON_EXPRESSION = "*/15 * * * *";

function parseList(value: string | undefined, fallback: string[]): string[] {
  if (!value) return fallback;
  const parts = value.split(",").map((s) => s.trim()).filter(Boolean);
  return parts.length > 0 ? parts : fallback;
}

let task: ScheduledTask | null = null;
let running = false;

async function runOnce(symbols: string[], timeframes: string[]): Promise<void> {
  if (running) {
    console.log(`[signalCron] ${new Date().toISOString()} prev_run_in_progress, skipping`);
    return;
  }
  running = true;
  const runStart = new Date().toISOString();
  console.log(`[signalCron] ${runStart} run_start pairs=${symbols.length}x${timeframes.length}`);
  try {
    for (const symbol of symbols) {
      for (const timeframe of timeframes) {
        const t = new Date().toISOString();
        try {
          const r = await generateSignal(symbol, timeframe);
          const parts = [`status=${r.status}`];
          if (r.reason) parts.push(`reason="${r.reason}"`);
          if (r.score !== undefined) parts.push(`score=${r.score}`);
          if (r.signalId) parts.push(`signalId=${r.signalId}`);
          console.log(`[signalCron] ${t} ${symbol}/${timeframe} ${parts.join(" ")}`);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          console.error(`[signalCron] ${t} ${symbol}/${timeframe} error="${msg}"`);
        }
      }
    }
  } finally {
    running = false;
    console.log(`[signalCron] ${new Date().toISOString()} run_end`);
  }
}

export function startScheduler(): void {
  if (task) return;
  const symbols = parseList(process.env.SIGNAL_SYMBOLS, DEFAULT_SYMBOLS);
  const timeframes = parseList(process.env.SIGNAL_TIMEFRAMES, DEFAULT_TIMEFRAMES);
  console.log(
    `[signalCron] scheduled "${CRON_EXPRESSION}" symbols=[${symbols.join(",")}] timeframes=[${timeframes.join(",")}]`,
  );
  task = cron.schedule(CRON_EXPRESSION, () => {
    void runOnce(symbols, timeframes);
  });
}

export function stopScheduler(): void {
  if (task) {
    task.stop();
    task = null;
    console.log("[signalCron] stopped");
  }
}

export async function runSchedulerOnce(): Promise<void> {
  const symbols = parseList(process.env.SIGNAL_SYMBOLS, DEFAULT_SYMBOLS);
  const timeframes = parseList(process.env.SIGNAL_TIMEFRAMES, DEFAULT_TIMEFRAMES);
  await runOnce(symbols, timeframes);
}
