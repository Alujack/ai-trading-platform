import { Router } from "express";
import { asyncHandler } from "../middleware/asyncHandler";
import { validate } from "../middleware/validate";
import {
  marketContextQuerySchema,
  type MarketContextQuery,
} from "../schemas/marketContext.schema";
import { getMarketContext, type MarketContextPayload } from "../services/marketContext";

export type { MarketContextPayload };

const router = Router();

router.get(
  "/market-context",
  validate(marketContextQuerySchema, "query"),
  asyncHandler(async (req, res) => {
    const { symbol, timeframe } = req.query as unknown as MarketContextQuery;
    res.json(await getMarketContext(symbol, timeframe));
  }),
);

export default router;
