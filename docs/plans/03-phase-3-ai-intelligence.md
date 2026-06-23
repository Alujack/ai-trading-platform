# Phase 3 Plan — Close the AI Intelligence Layer gaps

_Roadmap reference: `docs/trading_roadmap.md` Phase 3 (Week 9–12)._

The FastAPI service is wired with the Anthropic SDK and all three endpoints (`market-context`, `validate-signal`, `journal-review`) exist. What's missing is: a working API key, news ingestion to feed the AI, dashboard surfacing, and re-enabling the weekly review.

---

## §1 Set up the Anthropic API key + verify validate-signal actually runs

**Why:** `ANTHROPIC_API_KEY` is blank in both root `.env` and `apps/api/.env`. Right now the signal cron probably either short-circuits (if the Python side gracefully skips on missing key) or 500s silently — either way the AI scoring isn't real.

**Steps**
1. Get an Anthropic API key (console.anthropic.com).
2. Add to `.env` files at both `/.env` and `services/ai/.env` (note: services/ai may need its own `.env` — confirm via `services/ai/src/settings.py`).
3. Restart the AI service: `cd services/ai && source .venv/bin/activate && uvicorn src.main:app --reload`.
4. Smoke test from the terminal:
   ```bash
   curl -s -X POST http://localhost:8000/analyze/validate-signal \
     -H "Content-Type: application/json" \
     -d '{"symbol":"XAUUSD","timeframe":"60min","candles":[…],"indicators":[…],"news":[]}' | jq
   ```
   Should return `{ "score": …, "reasoning": "...", "approve": bool }`.
5. Watch `/tmp/ai-trading-logs/api.log` while the 15min signalCron fires — confirm `[signalCron] … score=NN` appears with a non-zero score and `signalId=…`.
6. **Important**: tail `services/ai` logs separately; if the AI service errors out, the signal generator should log `ai_service_unreachable` — fix any 401/quota errors before continuing.

**Done when:** A fresh cron tick produces a new `Signal` row whose `aiReasoning` is recognizably model-generated (not the local fallback string).

---

## §2 News ingestion → `NewsEvent` table

**Why:** Risk engine already reads `NewsEvent` for the news-blackout check; AI validator can use upcoming events as part of the prompt. Right now `NewsEvent` has **0 rows**, so both code paths are no-ops.

**Approach** — start with **one** source. Roadmap suggests three (NewsAPI, ForexFactory, Alpha Vantage news). Recommendation:
- **Alpha Vantage `NEWS_SENTIMENT` endpoint** (free tier supports it; key already configured in `.env.example`) for general news.
- **ForexFactory weekly calendar JSON** (`https://nfs.faireconomy.media/ff_calendar_thisweek.json`, no auth) for the high-impact event calendar — this is the one risk engine cares about.

**Steps**
1. New Python module `services/data/news_fetcher.py`:
   - `fetch_forexfactory_week() -> list[NewsEvent]` — parses the JSON, normalizes impact (`High/Medium/Low` → `HIGH/MEDIUM/LOW`), maps to currency.
   - `fetch_av_news(symbols) -> list[NewsEvent]` — calls Alpha Vantage NEWS_SENTIMENT, extracts headline + ticker mapping.
2. Upsert by `(title, scheduledAt)` unique pair to avoid dupes; add a unique constraint in Prisma if needed.
3. Schedule: ForexFactory once/day at 00:30 UTC (calendar refreshes weekly + intraday corrections); Alpha Vantage every 4h.
4. Wire into `services/data/main.py` cron loop or add a sibling scheduler.
5. Verify: after one run, `SELECT COUNT(*), impact FROM "NewsEvent" GROUP BY impact` returns rows; risk engine's news-blackout check now actually has data.

**Done when:** `NewsEvent` is populating; a manual `validateTrade()` call within ±30min of a HIGH event returns `approved=false, reason="news_blackout"`.

---

## §3 Surface Market Context on the dashboard

**Why:** The `/analyze/market-context` endpoint exists but nothing calls it. The roadmap explicitly lists "Market summary generated on dashboard" as Phase 3 "Done when" criteria.

**Steps**
1. Add a `GET /api/market-context?symbol=...&timeframe=...` route in apps/api:
   - Pulls recent candles + indicators + upcoming news from Postgres.
   - POSTs to `${AI_SERVICE_URL}/analyze/market-context`.
   - **Cache the response in Redis** with a 10-minute TTL (LLM calls are slow + expensive; the dashboard's selectors flip often).
   - Returns `{ summary: string, bias: "BULLISH"|"BEARISH"|"NEUTRAL", confidence: number, generatedAt: ISOString }`.
2. New web component `apps/web/app/components/MarketContextCard.tsx`:
   - Uses SWR with the symbol+timeframe in the cache key.
   - Renders the summary as prose, the bias as a colored pill, and shows `generatedAt`.
   - Loading state: shimmer for ~2s (the LLM call takes a beat); fallback message if the AI service is unreachable.
3. Wire it into `apps/web/app/page.tsx` above the chart (between PerformanceCard and the chart row).

**Done when:** Changing symbol in the Navbar produces a new market briefing within ~2s (cached) or ~5–10s (fresh). The bias pill updates accordingly.

---

## §4 Re-enable the weekly journal reviewer

**Why:** Roadmap §3.4 explicitly lists "Weekly: Feed all journal entries to Claude → detect patterns in losses." The code exists (`runWeeklyJournalReview()` in `apps/api/src/execution/scheduler.ts`) but `ENABLE_WEEKLY_REVIEW=false` in `apps/api/.env`.

**Steps**
1. Once §1 (API key) and §3 (a few journal rows exist) are done, flip `ENABLE_WEEKLY_REVIEW=true`.
2. For testing, manually trigger via `tsx -e "import('./src/execution/scheduler').then(m => m.runWeeklyReviewOnce())"` — same pattern we used for paperTrading.
3. Confirm a `Journal.aiReview` field gets populated, and that the cron logs `status=ok trades=N`.
4. **Stretch:** Surface the latest weekly review on the dashboard as an expandable section (similar to AI reasoning in the SignalsTable).

**Done when:** Manual trigger produces a new aiReview column populated with model output; the Sunday 00:00 UTC cron is scheduled.

---

## §5 (Stretch) AI-aware risk gating

**Why:** Currently the risk engine and the AI validator run in sequence but don't talk. If AI's confidence is 95 but the engine sees `dailyLoss > limit`, we still reject — that's correct. But the inverse is interesting: if AI confidence is only 72 (just above the 70 threshold) AND the news blackout window is approaching, the AI's reasoning could inform a sharper rejection.

**Skip for now.** Mention here only so it doesn't get forgotten.

---

## Order of operations

1. **§1 first** — without an API key, every other step's output is unverifiable.
2. **§2 second** — populates the NewsEvent table that §1's validator already wants.
3. **§3 third** — UI surfacing is satisfying but cosmetic until §1+§2 work.
4. **§4 last** — only meaningful once you have closed Journal entries (which requires paper trading to actually generate them; right now `Journal` is at 0 rows even though 7 trades closed — investigate why `monitorOpenTrades()` isn't creating Journal entries as expected).

> Note: zero Journal rows despite 7 closed trades is a small bug worth chasing as part of §4 prep. Check the `monitorOpenTrades()` close path in [apps/api/src/execution/paperTrading.ts:154-228](apps/api/src/execution/paperTrading.ts#L154-L228) — it should be inserting a Journal row per close.

## Estimated effort
- §1 API key + smoke test: **30 min**
- §2 News ingestion (FF calendar only first, then AV): **half a day**
- §3 Market Context API + UI card: **2–3 hr**
- §4 Weekly review enable + sanity check: **30 min**
- §5: skipped

**Total: ~1 day for §1–§4.**
