import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

/**
 * Runs the Python backtester (services/data/backtester.py --save-db) as a child
 * process so the dashboard can kick off a run without the CLI. One run at a time;
 * progress/result is exposed via getJobStatus() for the UI to poll. The child
 * persists its own BacktestRun row — we only parse its stdout for the new id.
 */

export interface RunOptions {
  timeframes?: string[];
  symbols?: string[];
  strategies?: string[];
  balance?: number;
  risk?: number;
  noCosts?: boolean;
  label?: string;
}

export interface JobStatus {
  running: boolean;
  startedAt: string | null;
  finishedAt: string | null;
  exitCode: number | null;
  ok: boolean | null;
  runId: string | null;
  error: string | null;
  tail: string; // last chunk of child output, for surfacing failures in the UI
}

const RUN_TIMEOUT_MS = 180_000;
const TAIL_MAX = 2_000;

const DEFAULTS = {
  timeframes: ["15min", "60min", "1min"],
  symbols: ["XAUUSD", "EURUSD", "BTCUSD"],
  strategies: ["trend_ema", "meanrev_rsi", "scalp_ema"],
  balance: 10_000,
  risk: 1,
};

let job: JobStatus = {
  running: false,
  startedAt: null,
  finishedAt: null,
  exitCode: null,
  ok: null,
  runId: null,
  error: null,
  tail: "",
};

// services/data lives four levels up from this file (apps/api/src/execution),
// and identically from the built apps/api/dist/execution.
function dataDir(): string {
  return path.resolve(__dirname, "../../../..", "services/data");
}

function pythonBin(dir: string): string {
  if (process.env.BACKTEST_PYTHON) return process.env.BACKTEST_PYTHON;
  const venv = path.join(dir, ".venv", "bin", "python");
  return existsSync(venv) ? venv : "python3";
}

function buildArgs(opts: RunOptions): string[] {
  const tfs = opts.timeframes?.length ? opts.timeframes : DEFAULTS.timeframes;
  const syms = opts.symbols?.length ? opts.symbols : DEFAULTS.symbols;
  const strats = opts.strategies?.length ? opts.strategies : DEFAULTS.strategies;
  const args = [
    "backtester.py",
    "--save-db",
    "--balance",
    String(opts.balance ?? DEFAULTS.balance),
    "--risk",
    String(opts.risk ?? DEFAULTS.risk),
    "--timeframes",
    ...tfs,
    "--symbols",
    ...syms,
    "--strategies",
    ...strats,
  ];
  if (opts.noCosts) args.push("--no-costs");
  if (opts.label) args.push("--label", opts.label);
  return args;
}

export function getJobStatus(): JobStatus {
  return job;
}

/** Returns false if a run is already in flight (caller should 409). */
export function startBacktest(opts: RunOptions): boolean {
  if (job.running) return false;

  const dir = dataDir();
  const script = path.join(dir, "backtester.py");
  if (!existsSync(script)) {
    job = {
      running: false,
      startedAt: new Date().toISOString(),
      finishedAt: new Date().toISOString(),
      exitCode: null,
      ok: false,
      runId: null,
      error: `backtester.py not found at ${script}`,
      tail: "",
    };
    return true; // job recorded (as failed); not a concurrency rejection
  }

  const py = pythonBin(dir);
  const args = buildArgs(opts);

  job = {
    running: true,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    exitCode: null,
    ok: null,
    runId: null,
    error: null,
    tail: "",
  };

  const child = spawn(py, args, { cwd: dir, env: process.env });
  let buf = "";
  const onData = (chunk: Buffer) => {
    buf += chunk.toString();
    if (buf.length > TAIL_MAX) buf = buf.slice(-TAIL_MAX);
    job.tail = buf;
  };
  child.stdout.on("data", onData);
  child.stderr.on("data", onData);

  const killTimer = setTimeout(() => {
    job.error = `timed out after ${RUN_TIMEOUT_MS / 1000}s`;
    child.kill("SIGKILL");
  }, RUN_TIMEOUT_MS);

  child.on("error", (err) => {
    clearTimeout(killTimer);
    job.running = false;
    job.finishedAt = new Date().toISOString();
    job.ok = false;
    job.error = `failed to start: ${err.message}`;
  });

  child.on("close", (code) => {
    clearTimeout(killTimer);
    const match = buf.match(/id=([a-f0-9]{24,32})/);
    job.running = false;
    job.finishedAt = new Date().toISOString();
    job.exitCode = code;
    job.ok = code === 0;
    job.runId = match ? match[1] : null;
    if (code !== 0 && !job.error) {
      job.error = `backtester exited with code ${code}`;
    }
    console.log(
      `[backtestRunner] finished code=${code} runId=${job.runId ?? "?"}` +
        (job.error ? ` error="${job.error}"` : ""),
    );
  });

  console.log(`[backtestRunner] started: ${py} ${args.join(" ")}`);
  return true;
}
