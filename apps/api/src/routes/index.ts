import { Router } from "express";
import aiProviderRouter from "./aiProvider.routes";
import candlesRouter from "./candles.routes";
import healthRouter from "./health.routes";
import marketContextRouter from "./marketContext.routes";
import performanceRouter from "./performance.routes";
import signalsRouter from "./signals.routes";
import symbolsRouter from "./symbols.routes";

const router = Router();

router.use(healthRouter);
router.use(candlesRouter);
router.use(symbolsRouter);
router.use(signalsRouter);
router.use(performanceRouter);
router.use(marketContextRouter);
router.use(aiProviderRouter);

export default router;
