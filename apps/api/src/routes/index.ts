import { Router } from "express";
import aiProviderRouter from "./aiProvider.routes";
import backtestsRouter from "./backtests.routes";
import brokersRouter from "./brokers.routes";
import candlesRouter from "./candles.routes";
import configRouter from "./config.routes";
import healthRouter from "./health.routes";
import journalRouter from "./journal.routes";
import marketContextRouter from "./marketContext.routes";
import newsRouter from "./news.routes";
import newsAlertRouter from "./newsAlert.routes";
import performanceRouter from "./performance.routes";
import positionsRouter from "./positions.routes";
import signalsRouter from "./signals.routes";
import streamRouter from "./stream.routes";
import symbolsRouter from "./symbols.routes";
import telegramRouter from "./telegram.routes";

const router = Router();

router.use(healthRouter);
router.use(candlesRouter);
router.use(symbolsRouter);
router.use(signalsRouter);
router.use(performanceRouter);
router.use(positionsRouter);
router.use(journalRouter);
router.use(marketContextRouter);
router.use(aiProviderRouter);
router.use(newsRouter);
router.use(newsAlertRouter);
router.use(streamRouter);
router.use(configRouter);
router.use(telegramRouter);
router.use(backtestsRouter);
router.use(brokersRouter);

export default router;
