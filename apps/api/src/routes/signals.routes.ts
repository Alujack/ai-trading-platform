import { Router } from "express";
import { prisma } from "../lib/prisma";
import { ser } from "../lib/decimal";
import { NotFoundError } from "../errors/httpError";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";
import {
  signalIdParamSchema,
  signalsQuerySchema,
  type SignalIdParam,
  type SignalsQuery,
} from "../schemas/signals.schema";

const router = Router();

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
