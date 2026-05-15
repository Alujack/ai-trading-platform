import { Router } from "express";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";

const router = Router();

router.get(
  "/symbols",
  asyncHandler(async (_req, res) => {
    const rows = await prisma.candle.findMany({
      distinct: ["symbol"],
      select: { symbol: true },
      orderBy: { symbol: "asc" },
    });
    res.json({ symbols: rows.map((r) => r.symbol) });
  }),
);

export default router;
