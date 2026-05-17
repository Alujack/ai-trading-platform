import { Router } from "express";
import candlesRouter from "./candles.routes";
import healthRouter from "./health.routes";
import performanceRouter from "./performance.routes";
import signalsRouter from "./signals.routes";
import symbolsRouter from "./symbols.routes";

const router = Router();

router.use(healthRouter);
router.use(candlesRouter);
router.use(symbolsRouter);
router.use(signalsRouter);
router.use(performanceRouter);

export default router;
