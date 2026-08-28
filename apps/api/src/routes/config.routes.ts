import { Router } from "express";
import { z } from "zod";
import { RISK_BOUNDS } from "../config/defaults";
import { RAW_FEED_FLAG, getFlag, setFlag } from "../config/flags";
import { getExecutionMap, resolveRiskConfig } from "../config/resolve";
import {
  armSystem,
  setKillSwitch,
  writeExecutionMode,
  writeRiskConfig,
} from "../config/store";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";
import { prisma } from "../lib/prisma";

const router = Router();

const ACTOR = "ui:dashboard";
const scopeEnum = z.enum(["GLOBAL", "STRATEGY", "SYMBOL"]);
const modeEnum = z.enum(["OFF", "AUTO", "CONFIRM"]);

// Build a Zod object for the numeric risk fields, bounded to the hard limits.
const riskFieldShape = Object.fromEntries(
  Object.entries(RISK_BOUNDS).map(([k, b]) => [
    k,
    (b.int ? z.number().int() : z.number()).min(b.min).max(b.max).optional(),
  ]),
) as Record<string, z.ZodOptional<z.ZodNumber>>;

const putRiskSchema = z
  .object({ scope: scopeEnum, scopeKey: z.string().max(40).optional().default(""), enabled: z.boolean().optional() })
  .extend(riskFieldShape);

const putExecSchema = z.object({
  scope: scopeEnum,
  scopeKey: z.string().max(40).optional().default(""),
  mode: modeEnum,
});

const reasonSchema = z.object({ reason: z.string().max(200).optional() });
const rawFeedSchema = z.object({ enabled: z.boolean() });
const riskQuerySchema = z.object({
  strategy: z.string().max(40).optional(),
  symbol: z.string().max(40).optional(),
});

// GET resolved effective risk config (optionally for a strategy/symbol context)
// plus every raw row and the field bounds (so the UI can render inputs).
router.get(
  "/config/risk",
  validate(riskQuerySchema, "query"),
  asyncHandler(async (req, res) => {
    const { strategy, symbol } = req.query as { strategy?: string; symbol?: string };
    const [effective, rows] = await Promise.all([
      resolveRiskConfig(strategy, symbol),
      prisma.riskConfig.findMany({ orderBy: [{ scope: "asc" }, { scopeKey: "asc" }] }),
    ]);
    res.json({ effective, rows, bounds: RISK_BOUNDS });
  }),
);

router.put(
  "/config/risk",
  validate(putRiskSchema, "body"),
  asyncHandler(async (req, res) => {
    const { scope, scopeKey, ...fields } = req.body as {
      scope: "GLOBAL" | "STRATEGY" | "SYMBOL";
      scopeKey: string;
    } & Record<string, number | boolean | undefined>;
    const result = await writeRiskConfig(ACTOR, scope, scopeKey, fields);
    if (!result.ok) {
      res.status(400).json({ error: result.error });
      return;
    }
    const effective = await resolveRiskConfig(
      scope === "STRATEGY" ? scopeKey : undefined,
      scope === "SYMBOL" ? scopeKey : undefined,
    );
    res.json({ ok: true, effective });
  }),
);

// GET all execution-mode rows + the effective global.
router.get(
  "/config/execution",
  asyncHandler(async (_req, res) => {
    const map = await getExecutionMap();
    res.json(map);
  }),
);

router.put(
  "/config/execution",
  validate(putExecSchema, "body"),
  asyncHandler(async (req, res) => {
    const { scope, scopeKey, mode } = req.body as {
      scope: "GLOBAL" | "STRATEGY" | "SYMBOL";
      scopeKey: string;
      mode: "OFF" | "AUTO" | "CONFIRM";
    };
    const result = await writeExecutionMode(ACTOR, scope, scopeKey, mode);
    if (!result.ok) {
      res.status(400).json({ error: result.error });
      return;
    }
    res.json({ ok: true, ...(await getExecutionMap()) });
  }),
);

// Panic: set GLOBAL mode = OFF. Signals still generate + log; nothing opens.
router.post(
  "/config/kill",
  validate(reasonSchema, "body"),
  asyncHandler(async (req, res) => {
    const reason = (req.body as { reason?: string }).reason ?? "";
    await setKillSwitch(reason ? `${ACTOR}:${reason}` : ACTOR);
    console.warn(`[config] KILL switch engaged reason="${reason}"`);
    res.json({ ok: true, ...(await getExecutionMap()) });
  }),
);

// Clear a manual kill: GLOBAL mode back to CONFIRM.
router.post(
  "/config/arm",
  validate(reasonSchema, "body"),
  asyncHandler(async (req, res) => {
    const reason = (req.body as { reason?: string }).reason ?? "";
    await armSystem(reason ? `${ACTOR}:${reason}` : ACTOR);
    console.warn(`[config] system ARMED (CONFIRM) reason="${reason}"`);
    res.json({ ok: true, ...(await getExecutionMap()) });
  }),
);

// ---------------------------------------------------------------------------
// Raw strategy feed ("layers off" view)
// ---------------------------------------------------------------------------
// This toggle is VISIBILITY ONLY. On, the gate records every strategy candidate
// untouched and stamps which layer stopped it, so the operator can trade the pure
// strategy signal by hand. It does not disable, relax or reorder a single check
// on the execution path: automation still needs AI + risk approval before a
// Signal exists, and the decider's caps/breakers still stand behind that.

router.get(
  "/config/raw-feed",
  asyncHandler(async (_req, res) => {
    res.json(await getFlag(RAW_FEED_FLAG));
  }),
);

router.put(
  "/config/raw-feed",
  validate(rawFeedSchema, "body"),
  asyncHandler(async (req, res) => {
    const { enabled } = req.body as { enabled: boolean };
    const state = await setFlag(ACTOR, RAW_FEED_FLAG, enabled);
    console.warn(`[config] raw signal feed ${enabled ? "ENABLED" : "disabled"} (observe-only)`);
    res.json({ ok: true, ...state });
  }),
);

export default router;
