import { Router } from "express";
import { z } from "zod";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";

const router = Router();

const querySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(30),
});

function num(v: { toString(): string } | null | undefined): number | null {
  return v == null ? null : Number(v.toString());
}

// Trade journal: notes + AI review per trade, with the trade/signal context the
// dashboard needs to render each entry.
router.get(
  "/journal",
  validate(querySchema, "query"),
  asyncHandler(async (req, res) => {
    const { limit } = req.query as unknown as z.infer<typeof querySchema>;

    const entries = await prisma.journal.findMany({
      orderBy: { createdAt: "desc" },
      take: limit,
      include: {
        trade: {
          select: {
            status: true,
            profitLoss: true,
            openedAt: true,
            closedAt: true,
            signal: { select: { symbol: true, direction: true, strategyName: true } },
          },
        },
      },
    });

    const data = entries.map((e) => ({
      id: e.id,
      notes: e.notes,
      aiReview: e.aiReview,
      emotions: e.emotions,
      createdAt: e.createdAt.toISOString(),
      symbol: e.trade.signal.symbol,
      direction: e.trade.signal.direction,
      strategyName: e.trade.signal.strategyName,
      status: e.trade.status,
      profitLoss: num(e.trade.profitLoss),
      closedAt: e.trade.closedAt ? e.trade.closedAt.toISOString() : null,
    }));

    res.json({ data, count: data.length });
  }),
);

export default router;
