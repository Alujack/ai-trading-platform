import { Router } from "express";
import { z } from "zod";
import { prisma } from "../lib/prisma";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";

const router = Router();

const querySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(25),
  impact: z.enum(["LOW", "MEDIUM", "HIGH"]).optional(),
});

// Latest economic-calendar + AI-summarized news the n8n layer ingested.
router.get(
  "/news",
  validate(querySchema, "query"),
  asyncHandler(async (req, res) => {
    const { limit, impact } = req.query as unknown as z.infer<typeof querySchema>;

    const rows = await prisma.newsEvent.findMany({
      where: impact ? { impact } : undefined,
      orderBy: { scheduledAt: "desc" },
      take: limit,
      select: {
        id: true,
        title: true,
        impact: true,
        currency: true,
        scheduledAt: true,
        actual: true,
        forecast: true,
        previous: true,
        aiSummary: true,
      },
    });

    const now = Date.now();
    const data = rows.map((r) => ({
      ...r,
      scheduledAt: r.scheduledAt.toISOString(),
      upcoming: r.scheduledAt.getTime() > now,
    }));

    res.json({ data, count: data.length });
  }),
);

export default router;
