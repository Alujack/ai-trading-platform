import { Router } from "express";
import { z } from "zod";
import { getJobStatus, startBacktest } from "../execution/backtestRunner";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";

const router = Router();

const runSchema = z.object({
  timeframes: z.array(z.enum(["1min", "5min", "15min", "60min", "daily"])).min(1).max(5).optional(),
  symbols: z.array(z.enum(["XAUUSD", "EURUSD", "BTCUSD"])).min(1).optional(),
  strategies: z.array(z.enum(["trend_ema", "meanrev_rsi", "scalp_ema"])).min(1).optional(),
  balance: z.number().positive().max(1e9).optional(),
  risk: z.number().positive().max(100).optional(),
  noCosts: z.boolean().optional(),
  label: z.string().max(80).optional(),
});

// Kick off a backtest (spawns the Python backtester with --save-db). One at a
// time. The UI polls /backtests/run/status, then refreshes the run list.
router.post(
  "/backtests/run",
  validate(runSchema, "body"),
  asyncHandler(async (req, res) => {
    const started = startBacktest(req.body);
    if (!started) {
      res.status(409).json({ error: "A backtest is already running" });
      return;
    }
    res.status(202).json({ status: "started", job: getJobStatus() });
  }),
);

// Status of the most recent / in-flight run (registered before :id).
router.get(
  "/backtests/run/status",
  asyncHandler(async (_req, res) => {
    res.json(getJobStatus());
  }),
);

/**
 * Backtest runs persisted by services/data/backtester.py (--save-db). The data
 * service computes everything; the API just serves stored rows to the dashboard.
 */

// List recent runs. Includes the result metrics (small) so the list page can
// render summaries without a second fetch, but omits the heavier equity curves.
router.get(
  "/backtests",
  asyncHandler(async (_req, res) => {
    const runs = await prisma.backtestRun.findMany({
      orderBy: { createdAt: "desc" },
      take: 50,
      select: {
        id: true,
        label: true,
        startingBalance: true,
        riskPct: true,
        costsApplied: true,
        config: true,
        results: true,
        createdAt: true,
      },
    });

    res.json({
      runs: runs.map((r) => ({
        id: r.id,
        label: r.label,
        startingBalance: Number(r.startingBalance.toString()),
        riskPct: Number(r.riskPct.toString()),
        costsApplied: r.costsApplied,
        config: r.config,
        results: r.results,
        createdAt: r.createdAt.toISOString(),
      })),
    });
  }),
);

// Full detail for one run, including per-result equity curves for charting.
router.get(
  "/backtests/:id",
  asyncHandler(async (req, res) => {
    const run = await prisma.backtestRun.findUnique({
      where: { id: req.params.id },
    });
    if (!run) {
      res.status(404).json({ error: "Backtest run not found" });
      return;
    }
    res.json({
      id: run.id,
      label: run.label,
      startingBalance: Number(run.startingBalance.toString()),
      riskPct: Number(run.riskPct.toString()),
      costsApplied: run.costsApplied,
      config: run.config,
      results: run.results,
      equityCurves: run.equityCurves,
      createdAt: run.createdAt.toISOString(),
    });
  }),
);

export default router;
