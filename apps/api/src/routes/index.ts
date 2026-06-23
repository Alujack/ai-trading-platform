import { Router } from "express";
import aiProviderRouter from "./aiProvider.routes";
import candlesRouter from "./candles.routes";
import healthRouter from "./health.routes";
import marketContextRouter from "./marketContext.routes";
import newsRouter from "./news.routes";
import newsAlertRouter from "./newsAlert.routes";
import performanceRouter from "./performance.routes";
import positionsRouter from "./positions.routes";
import signalsRouter from "./signals.routes";
import streamRouter from "./stream.routes";
import symbolsRouter from "./symbols.routes";

const router = Router();

router.use(healthRouter);
router.use(candlesRouter);
router.use(symbolsRouter);
router.use(signalsRouter);
router.use(performanceRouter);
router.use(positionsRouter);
router.use(marketContextRouter);
router.use(aiProviderRouter);
router.use(newsRouter);
router.use(newsAlertRouter);
router.use(streamRouter);

export default router;
