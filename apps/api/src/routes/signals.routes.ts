import type { Prisma } from "@prisma/client";
import { Router } from "express";
import { RAW_FEED_FLAG, getFlag } from "../config/flags";
import { prisma } from "../lib/prisma";
import { ser } from "../lib/decimal";
import { NotFoundError } from "../errors/httpError";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";
import { gateCandidate, type SignalCandidate } from "../signals/gate";
import {
  isBlockedOnly,
  rawSignalsQuerySchema,
  signalCandidateSchema,
  signalIdParamSchema,
  signalsQuerySchema,
  type RawSignalsQuery,
  type SignalCandidateBody,
  type SignalIdParam,
  type SignalsQuery,
} from "../schemas/signals.schema";

const router = Router();

// Single AI + risk gate for every strategy. A strategy (Python or TS) POSTs a
// candidate here; only AI-approved, risk-approved candidates become PENDING.
router.post(
  "/signals/candidate",
  validate(signalCandidateSchema, "body"),
  asyncHandler(async (req, res) => {
    const candidate = req.body as unknown as SignalCandidateBody;
    const result = await gateCandidate(candidate satisfies SignalCandidate);
    res.status(result.status === "generated" ? 201 : 200).json(result);
  }),
);

router.get(
  "/signals",
  validate(signalsQuerySchema, "query"),
  asyncHandler(async (req, res) => {
    const { limit, offset, status, symbol } = req.query as unknown as SignalsQuery;
    const where = {
      ...(status ? { status } : {}),
      ...(symbol ? { symbol } : {}),
    };
    const [data, total] = await prisma.$transaction([
      prisma.signal.findMany({
        where,
        orderBy: { createdAt: "desc" },
        take: limit,
        skip: offset,
      }),
      prisma.signal.count({ where }),
    ]);
    res.json(
      ser({
        data,
        pagination: { limit, offset, total },
      }),
    );
  }),
);

// The PURE strategy feed — every candidate as the strategy emitted it, with the
// verdict of each protection layer attached (blockedBy/blockedReason) instead of
// applied. Read-only and execution-free: these rows have no path to a Trade.
// Populated only while the raw_signal_feed flag is on.
//
// Registered BEFORE /signals/:id so the literal path wins over the param route.
router.get(
  "/signals/raw",
  validate(rawSignalsQuerySchema, "query"),
  asyncHandler(async (req, res) => {
    const { limit, offset, symbol, strategy, timeframe, verdict, blockedOnly } =
      req.query as unknown as RawSignalsQuery;
    const where: Prisma.RawSignalWhereInput = {
      ...(symbol ? { symbol } : {}),
      ...(strategy ? { strategyName: strategy } : {}),
      ...(timeframe ? { timeframe } : {}),
      ...(verdict ? { verdict } : {}),
      // blockedOnly wins over an explicit verdict: "show me what automation didn't take".
      ...(isBlockedOnly(blockedOnly) ? { verdict: { in: ["REJECTED", "SKIPPED"] } } : {}),
    };
    const [data, total, feed] = await Promise.all([
      prisma.rawSignal.findMany({
        where,
        orderBy: { lastSeenAt: "desc" },
        take: limit,
        skip: offset,
      }),
      prisma.rawSignal.count({ where }),
      getFlag(RAW_FEED_FLAG),
    ]);
    res.json(
      ser({
        data,
        feedEnabled: feed.enabled,
        pagination: { limit, offset, total },
      }),
    );
  }),
);

router.get(
  "/signals/:id",
  validate(signalIdParamSchema, "params"),
  asyncHandler(async (req, res) => {
    const { id } = req.params as unknown as SignalIdParam;
    const signal = await prisma.signal.findUnique({
      where: { id },
      include: { trades: { include: { journals: true } } },
    });
    if (!signal) throw new NotFoundError();
    res.json(ser(signal));
  }),
);

export default router;
