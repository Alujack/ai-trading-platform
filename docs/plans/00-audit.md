# Roadmap Audit — Phases 1, 2, 3

_Snapshot taken against `docs/trading_roadmap.md` based on actual files, DB schema, and runtime probes._

## Top-line

| Phase | Roadmap says "done when…" | Verdict |
|---|---|---|
| **Phase 1** Data Foundation | Docker up · candles flowing · can query last 100 candles | **✓ Done** (gaps in news ingestion, TimescaleDB hypertable, RiskLog writes) |
| **Phase 2** Analysis Engine | Indicators stored · dashboard shows live chart + indicator values | **✓ Done** (gaps in S/R + HH/LL detection; EMA200 not displayed) |
| **Phase 3** AI Intelligence | Market summary on dashboard · news analyzed · signal validation w/ reasoning | **◐ Partial** — endpoints exist but news ingestion + market-context UI are missing |

You've front-loaded a lot of Phase 4–6 (paper trading, risk engine, performance) but left earlier-phase pieces unfinished. The plans below close those gaps before moving forward.

---

## Phase 1 — Data Foundation

### Done
- ✓ Monorepo layout matches roadmap exactly (`apps/web`, `apps/api`, `services/data`, `services/ai`, `packages/shared`)
- ✓ `docker-compose.yml` with Postgres + TimescaleDB + Redis (extension confirmed: `SELECT extname FROM pg_extension` returns `timescaledb`)
- ✓ Data ingestion: [services/data/fetcher.py](services/data/fetcher.py) (Twelve Data + Alpha Vantage fallback) + [services/data/main.py:47-82](services/data/main.py#L47-L82) (per-timeframe scheduled loop)
- ✓ All 7 roadmap tables present in [prisma/schema.prisma](apps/api/prisma/schema.prisma): `Candle`, `Indicator`, `NewsEvent`, `Signal`, `Trade`, `Journal`, `RiskLog`
- ✓ Can query last 100 candles — **905 candles** in DB across multiple timeframes
- ✓ Cron jobs both on the Python side (per-timeframe ingestion) and the TS side (`signalCron`, `paperCron`)

### Gaps
- ✗ **TimescaleDB extension installed but no hypertables.** `Candle` is a plain Postgres table. Time-series query performance won't scale. → Phase 1 plan §1
- ✗ **`NewsEvent` is empty** — 0 rows. No news ingestion runs anywhere. → Phase 3 plan §2
- ✗ **`RiskLog` is empty** — riskEngine computes position size + circuit breakers but never persists decisions. → Phase 1 plan §2
- ✗ **Indicator coverage gap** — 905 candles, 740 indicators. Some symbol/timeframe combos have no indicators. → Phase 2 plan §3
- ✗ **`services/data/src/worker.py` is a stub** that conflicts with the real entry (`services/data/main.py`). → Phase 1 plan §3
- ✗ Fetcher / db modules have no tests.

---

## Phase 2 — Analysis Engine

### Done
- ✓ Python uses `pandas_ta_classic` in [services/data/indicator_calculator.py:21](services/data/indicator_calculator.py#L21)
- ✓ RSI(14), EMA20/50/200, ATR(14) all computed and stored on the `Indicator` table
- ✓ TradingView widget embedded in dashboard ([apps/web/app/components/TradingViewChart.tsx](apps/web/app/components/TradingViewChart.tsx))
- ✓ Live indicator values render via SWR with 30s refresh ([apps/web/app/components/IndicatorSidebar.tsx](apps/web/app/components/IndicatorSidebar.tsx))
- ✓ Symbol + timeframe switcher in Navbar (XAUUSD, EURUSD, BTCUSD × 1min/5min/15min/60min/daily)

### Gaps
- ✗ **Support/Resistance detection** — roadmap §2.2 lists it; not implemented anywhere. → Phase 2 plan §1
- ✗ **Higher-High / Lower-Low (trend structure)** — roadmap §2.2 lists it; not implemented. → Phase 2 plan §2
- ✗ **EMA200 not displayed** in IndicatorSidebar even though it's computed. Easy win. → Phase 2 plan §4
- ✗ **Chart and indicators are disconnected.** TradingView shows TV's own feed, not your DB. There's no visual overlay of your computed levels (S/R, EMAs) on the chart. → Phase 2 plan §5 (stretch)

---

## Phase 3 — AI Intelligence Layer

### Done
- ✓ FastAPI service ([services/ai/src/main.py:28-68](services/ai/src/main.py#L28-L68)) with three endpoints:
  - `/analyze/market-context`
  - `/analyze/validate-signal`
  - `/analyze/journal-review`
- ✓ Anthropic SDK wired in [services/ai/src/llm.py](services/ai/src/llm.py) with structured output parsing
- ✓ Signal validator already integrated into the signal pipeline — confidence score + reasoning land in `Signal.aiReasoning`
- ✓ Weekly journal review code exists (`runWeeklyJournalReview()`) — just gated off by `ENABLE_WEEKLY_REVIEW`

### Gaps
- ✗ **`ANTHROPIC_API_KEY` is blank** in both `.env` files. AI calls will fail at runtime. → Phase 3 plan §1
- ✗ **Market-context endpoint exists but no UI calls it.** No "market briefing" card on the dashboard. → Phase 3 plan §3
- ✗ **News ingestion entirely missing.** No NewsAPI / ForexFactory / Alpha Vantage news fetcher. `NewsEvent` table stays at 0 rows. → Phase 3 plan §2
- ✗ **News analyzer not wired.** Without news flowing in, the AI never sees event context.
- ✗ **Weekly journal review disabled** (`ENABLE_WEEKLY_REVIEW=false`).

---

## Recommended order

1. **Phase 1 plan** (§1–§3) — small, quick wins. Get the foundation solid.
2. **Phase 3 plan** (§1–§2) — set the API key + add news ingestion. Without news + a working API key, Phase 3's value is dormant.
3. **Phase 2 plan** (§1–§4) — S/R and HH/LL detection feed strategy quality.
4. **Phase 3 plan** (§3) — wire market-context to the dashboard last, because it benefits from §1/§2 first.

Each phase plan is a separate file in this directory.
