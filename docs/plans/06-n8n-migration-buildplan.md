# Phase 5 — n8n News-Automation Migration (Build Plan)

_The actionable companion to `05-news-ingestion-n8n.md`. That doc is the **why/what** (research + tool choice); this is the **how** (milestones, files, acceptance criteria)._

**Decisions locked in (2026-06-23):**

1. **Full migration.** The existing Python fetcher `services/data/news_fetcher.py` (ForexFactory + Alpha Vantage) is **retired**. Both feeds are rebuilt as n8n workflows. The Python worker goes back to a single responsibility: candles → indicators → strategies.
2. **AI stays behind our service.** n8n does **not** call Claude/Gemini directly. The breaking-news workflow POSTs to a new `services/ai` endpoint so the **Mock / Claude / Gemini runtime toggle** in the dashboard remains the single source of truth for which model runs.

> ⚠️ This migration removes working code. Do it behind a feature switch (env flag), verify n8n is writing rows, **then** delete the Python path — never both off at once. See Milestone 4.

---

## What already exists (do not rebuild)

| Piece | Where | Status |
|---|---|---|
| `NewsEvent` table, `@@unique([title, scheduledAt])` | `apps/api/prisma/schema.prisma:65` | ✅ keep |
| News consumed by risk gate | `riskEngine.isNewsWindow()` (HIGH events, ±30 min) | ✅ keep |
| Signals query upcoming news | `marketContext.routes.ts:93` (`newsEvent.findMany`) | ✅ keep |
| AI provider toggle | `services/ai` `/provider` + `AiProviderToggle.tsx` | ✅ reuse |
| ForexFactory + Alpha Vantage fetchers | `services/data/news_fetcher.py` | ❌ **retire (Milestone 4)** |
| News loops in worker | `main.py` `forexfactory` + `alpha_vantage` `_periodic_loop`s | ❌ **remove (Milestone 4)** |

The consumption side is untouched. Once n8n writes `NewsEvent` rows, the risk gate keeps working with **zero trading-code changes**.

---

## Milestone 0 — Infra: n8n in the Compose stack (≈1–2h)

**Goal:** n8n running, with its own database, sharing the trading Postgres instance.

1. Create a dedicated database for n8n's internal state (keep it out of the trading schema):
   - Add an init step (`infra/postgres/init/01-n8n-db.sql`: `CREATE DATABASE n8n;`) mounted into the Postgres container's `/docker-entrypoint-initdb.d`, **or** create it manually once.
2. Add the `n8n` service to `docker-compose.yml` (alongside `postgres` + `redis`):

```yaml
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    container_name: trading-n8n
    restart: unless-stopped
    ports:
      - "${N8N_PORT:-5678}:5678"
    environment:
      - DB_TYPE=postgresdb
      - DB_POSTGRESDB_HOST=postgres
      - DB_POSTGRESDB_PORT=5432
      - DB_POSTGRESDB_DATABASE=n8n
      - DB_POSTGRESDB_USER=${POSTGRES_USER:-postgres}
      - DB_POSTGRESDB_PASSWORD=${POSTGRES_PASSWORD:-postgres}
      - N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}   # required; never hardcode
      - GENERIC_TIMEZONE=UTC
      - TZ=UTC
      - N8N_DIAGNOSTICS_ENABLED=false
    volumes:
      - n8n-data:/home/node/.n8n
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  n8n-data:
```

3. Add `N8N_ENCRYPTION_KEY` and `N8N_PORT` to `.env.example` (and your local `.env`, gitignored). Generate the key with `openssl rand -hex 24`.
4. `docker compose up -d n8n` → open `http://localhost:5678`, create the owner account.

**Acceptance:** n8n UI loads; `n8n` database has tables; trading data untouched.

---

## Milestone 1 — Postgres credential for the trading DB (≈30m)

n8n's **Postgres node** (the one that writes `NewsEvent`) points at the **trading** database, not the `n8n` one.

1. In n8n → Credentials → Postgres: host `postgres`, port `5432`, database `trading`, user/pass from env.
2. Test the connection. Run `SELECT count(*) FROM "NewsEvent";` from a manual Postgres node to confirm reachability and that the quoted table name resolves.

**Acceptance:** a manual node returns the current `NewsEvent` count.

---

## Milestone 2 — Workflow A: Economic calendar → `NewsEvent` (≈half day)

Rebuilds the ForexFactory path. **This is the high-value one** — it feeds the risk gate.

```
Schedule Trigger (every 6h)
  → HTTP Request  GET https://nfs.faireconomy.media/ff_calendar_thisweek.json
  → Code (JS)     normalize each event (mirror news_fetcher.py logic)
  → Filter        drop rows where impact is null / no currency / unparseable date
  → Remove Duplicates   key = currency|title|scheduledAt
  → Postgres      upsert into "NewsEvent"  (ON CONFLICT (title, scheduledAt) DO UPDATE)
```

**Port these exact rules from `news_fetcher.py` (so behavior is identical):**

- **Impact map:** `high→HIGH, medium→MEDIUM, low→LOW`; anything else (Holiday, blank) → **drop the row**. The gate only acts on `HIGH`, so HIGH fidelity is what matters.
- **`scheduledAt` → UTC, stored naive.** FF dates are ISO-8601 with offset (`...-05:00`). Convert to UTC and **strip the offset** — the columns are `TIMESTAMP WITHOUT TIME ZONE` and the risk engine compares against `now` as UTC. Getting this wrong blocks trades at the wrong times (the #1 bug risk).
- **`currency`** = FF `country` field, upper-cased; empty → `UNKNOWN`.
- **`forecast`/`previous`/`actual`** as nullable strings; `actual` fills in on a later run after release.
- **Upsert key** must match the schema: `@@unique([title, scheduledAt])`. (Optional later: migrate to `@@unique([currency, title, scheduledAt])` per `05`§6 if title collisions across currencies appear — not needed for MVP.)

**Acceptance:**
- After one manual run, `SELECT count(*) FROM "NewsEvent" WHERE impact='HIGH'` is non-zero.
- Spot-check a known event (e.g. next CPI/NFP): `scheduledAt` matches the real UTC release time.
- A second run adds **zero** duplicate rows.

---

## Milestone 3 — Workflow B: Breaking news → AI summary → `NewsEvent` (≈half day)

Rebuilds the Alpha Vantage path **and** adds the AI-summary layer the Python version never had.

### 3a. New AI endpoint (keeps the toggle in charge)

Add to `services/ai`:

- `POST /analyze/news-summary` — input: a batch of `{title, source, publishedAt}`; output (structured, validated by Pydantic): `{summary, impact: LOW|MEDIUM|HIGH, currency, rationale}`. Implement exactly like `validate_signal` in `main.py:81` — call `analyze(...)` with a new `NEWS_SUMMARY_SYSTEM` prompt and response model. Because it routes through `analyze()`, it automatically respects the active provider (Mock/Claude/Gemini).
- Add `NewsSummaryRequest`/`Response` to `schemas.py` and `NEWS_SUMMARY_SYSTEM` to `prompts.py`.

### 3b. The workflow

```
Schedule Trigger (every 15–30 min)
  → [parallel] HTTP Request (Alpha Vantage NEWS_SENTIMENT)   +   RSS Read (Fed / ECB / BoJ)
  → Merge
  → Remove Duplicates (by article URL/title)
  → Filter (FX-relevant: currency keywords / central-bank terms)
  → HTTP Request  POST {API or AI service}/analyze/news-summary   ← respects the provider toggle
  → Code (parse structured JSON)
  → Postgres (insert; aiSummary, impact, currency, scheduledAt = publishedAt)
```

- Route the HTTP node at `services/ai` directly (`http://ai:8000/analyze/news-summary`) **or** proxy through `apps/api` — either keeps the toggle authoritative since both hit `analyze()`.
- Alpha Vantage key stays in n8n credentials, **not** in code.

**Acceptance:** headlines land in `NewsEvent` with a non-null `aiSummary`; flipping the dashboard toggle to Mock makes new summaries carry the mock tag; flipping to Gemini uses Gemini.

---

## Milestone 4 — Decommission the Python news path (≈1h) — _only after M2 + M3 verified_

1. Confirm n8n has been writing fresh `NewsEvent` rows for a full cycle (check `createdAt`).
2. In `services/data/main.py`:
   - Remove `from news_fetcher import ingest_alpha_vantage_news, ingest_forexfactory` (`main.py:21`).
   - Remove the two `_periodic_loop("forexfactory", …)` and `_periodic_loop("alpha_vantage", …)` entries (`main.py:130–131`).
   - **Keep** `_periodic_loop("strategy_runner", …)` and all candle/indicator loops.
   - Drop now-unused constants `FOREXFACTORY_PERIOD_SECONDS`, `AV_NEWS_PERIOD_SECONDS`.
3. Delete `services/data/news_fetcher.py`.
4. Remove the news-fetch tests under `services/data/tests/` that target it; keep everything else.
5. Drop `ALPHA_VANTAGE_KEY` from the worker's env (it moves to n8n).

**Acceptance:** worker boots and logs only candle/indicator/strategy loops; `pytest` green; `NewsEvent` row count keeps climbing (now from n8n).

---

## Milestone 5 — Optional: high-impact alert webhook → API (≈2h)

So the system reacts to a HIGH event *now* instead of at the next signal cycle.

```
(branch off Workflow A, after upsert)
  → IF impact == HIGH AND scheduledAt within next 60 min
  → HTTP Request  POST http://api:4000/internal/news-alert  { title, currency, scheduledAt }
```

- Add `apps/api/src/routes/newsAlert.routes.ts` (mount in `routes/index.ts` like the others). For MVP it can just log + cache the active blackout in Redis; the gate already enforces the window, so this is a UX/proactivity nicety, not a correctness requirement.

**Acceptance:** posting a synthetic HIGH event hits the endpoint and surfaces a dashboard banner / log line.

---

## Milestone 6 — Version-control the workflows (≈30m)

n8n workflows live in its DB by default — that's not reviewable. Export and commit them.

1. Export each workflow as JSON (n8n → workflow → ⋯ → Download).
2. Commit to `infra/n8n/workflows/workflow-a-calendar.json` and `workflow-b-news-ai.json`.
3. Add a short `infra/n8n/README.md`: how to import them into a fresh n8n, which credentials they expect, the schedule cadences.

**Acceptance:** a teammate can stand up n8n from Compose, import both JSONs, wire two credentials, and have ingestion running.

---

## Sequencing & effort

| Order | Milestone | Effort | Blocking? |
|---|---|---|---|
| 1 | M0 infra | 1–2h | yes — everything needs n8n up |
| 2 | M1 DB credential | 30m | yes for M2/M3 |
| 3 | **M2 calendar** | half day | **highest value** — feeds the gate |
| 4 | M3 news + AI | half day | needs M3a endpoint first |
| 5 | M4 decommission | 1h | **only after M2+M3 verified** |
| 6 | M5 alert webhook | 2h | optional |
| 7 | M6 commit workflows | 30m | do before calling it done |

**Total core (M0–M4, M6): ~1.5 days.** M5 optional.

---

## Risks & guardrails

- **Timezone (#1 risk).** Everything UTC end-to-end. The risk engine does ±30-min window math on `scheduledAt`. Verify against a known release before trusting it.
- **Don't delete before verifying.** M4 strictly follows M2+M3 acceptance. Run both paths in parallel for one cycle if nervous (n8n upserts are idempotent against the same unique key, so dual-writing is safe).
- **ToS / rate limits.** Keep ForexFactory polling gentle (every 6h is plenty — its weekly file changes slowly). Alpha Vantage free tier is 25 req/day; the 15–30 min cadence in M3 will blow that — gate it to market hours or widen the interval. Re-verify all free-tier limits (they drift).
- **Secrets.** `N8N_ENCRYPTION_KEY`, Postgres pw, Alpha Vantage key all via n8n credentials / `.env`. Never in committed workflow JSON — n8n exports reference credentials by name, not value; confirm before committing.
- **Single point of failure.** News now depends on the n8n container being up. Add it to `restart: unless-stopped` (done in M0) and watch the execution history.

---

## CLAUDE.md compliance check

- ✅ Risk engine still called before execution — untouched; it just gets fed better data.
- ✅ No hardcoded keys — all via n8n credentials / `.env`.
- ✅ Trade journaling / backtest / paper-trade rules — unaffected by this migration.
