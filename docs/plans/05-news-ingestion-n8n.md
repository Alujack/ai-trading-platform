# News Ingestion with n8n — Report & Workflow Design

_Fills the Phase 3 gap from `00-audit.md` ("News ingestion entirely missing — `NewsEvent` table stays at 0 rows"). Pairs with `research/forex-strategy-survey.md` (high-impact-event avoidance is a core risk control) and `docs/plans/04-strategy-support-roadmap.md`._

_All pricing/feature claims are dated **June 2026** and should be re-verified before you commit — these services change fast._

---

## TL;DR recommendation

**Use n8n, self-hosted in your existing Docker Compose stack, as the news-ingestion layer that writes into your `NewsEvent` table.** It's the right tool here: the job is "poll a few sources on a schedule, normalize, dedupe, optionally summarize with Claude, upsert into Postgres" — which is exactly n8n's sweet spot, and it keeps news plumbing *out* of your trading code. Your `services/data` Python worker stays focused on candles/indicators/strategies; n8n becomes a separate, visually-debuggable feeder.

Two workflows:

1. **Economic-calendar ingestion** (scheduled events → `impact`, `currency`, `scheduledAt`, `forecast`, `previous`, `actual`). This is the one that matters most — it directly feeds `riskEngine.isNewsWindow()`, which already blocks trades within 30 minutes of HIGH-impact events.
2. **Breaking-news ingestion + AI summary** (headlines → Claude summary/impact tag → `aiSummary`). Feeds the AI market-context endpoint.

Recommended source mix (all free or low-cost, ToS-compliant): **central-bank RSS** (Fed/ECB/BoJ, free) + **a calendar source** (see the honest caveat in §3) + **Marketaux** and/or **Alpha Vantage news** for headlines.

---

## 1. Why n8n fits (and where it doesn't)

n8n is a fair-code workflow automation tool (Sustainable Use License). The **Community Edition is free, self-hosted, with unlimited executions** and the full node catalog — which is all you need here. ([n8n pricing](https://n8n.io/pricing/), [Sustainable Use License](https://docs.n8n.io/sustainable-use-license/))

What makes it a good fit for this specific job:

- **Self-hosts in Docker Compose** alongside your existing Postgres + Redis — official image and compose guide exist, and it can use Postgres as its own backing store and Redis for queue mode if you ever scale. ([Docker hosting docs](https://docs.n8n.io/hosting/installation/docker/))
- **Schedule Trigger** node gives cron-style cadences (every N minutes/hours, daily, weekly) with no APScheduler/Celery glue. ([Schedule Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/))
- **The exact nodes this job needs** are built in: HTTP Request, RSS Read / RSS Feed Trigger, Postgres (insert/upsert), Code (JS or Python), IF/Filter/Merge, and **Remove Duplicates** for dedupe. ([core nodes](https://docs.n8n.io/integrations/builtin/core-nodes/), [Remove Duplicates](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.removeduplicates/))
- **Native Anthropic/Claude node** (LangChain-based) for summarizing or impact-tagging news, with structured output — you already use Claude in `services/ai`, so this is consistent. ([Anthropic node](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-langchain.anthropic/))
- **Visual execution history** — you can see the payload at each step, which beats grepping Python logs when a feed changes its shape.

Where n8n would be the *wrong* call: very high-frequency polling (sub-minute across many sources), or heavy multi-stage transforms better done in Pandas. Neither applies — news polls every 5–30 minutes.

### n8n vs. extending your Python worker

| | n8n (self-hosted) | Custom Python in `services/data` |
|---|---|---|
| Time to working pipeline | Hours (visual, prebuilt nodes) | Days (HTTP, retries, scheduler, dedupe by hand) |
| Retries / backoff | Built into each node | You write it |
| Observability | Execution UI with per-node payloads | Logs you build |
| Source changes | Edit nodes, no redeploy | Code change + redeploy |
| Keeps trading code clean | Yes — separate service | News logic mixed into worker |
| Extra infra | One container | None (reuses worker) |
| Cost | Free (Community) | Free |

Given you already have a Python worker, the temptation is to "just add a fetcher." That works, but it couples news scraping (brittle, frequently-changing feeds) to your trading engine. n8n isolates that churn. **Recommendation: n8n for ingestion; Python worker stays the consumer** (it already reads `NewsEvent` indirectly — `signalGenerator.ts` queries upcoming news and passes it to the risk engine).

---

## 2. Hosting recommendation: self-hosted in your Compose stack

Your `docker-compose.yml` already runs Postgres (+ TimescaleDB) and Redis. Add n8n as one more service. **Community Edition, free, unlimited executions** — no reason to pay for Cloud for this.

For reference, n8n Cloud pricing (June 2026, [n8n pricing](https://n8n.io/pricing/), corroborated by [third-party guides](https://www.lowcode.agency/blog/n8n-pricing)): Starter ~$20/mo (2,500 executions), Pro ~$50/mo (10K), Business ~$667/mo (40K). As of April 2026 n8n removed active-workflow limits, so plans bill purely on executions. **You don't need any of this** — self-hosted Community has none of those caps.

Sketch of the service to add (illustrative — set a strong `N8N_ENCRYPTION_KEY`, never hardcode secrets per `CLAUDE.md`):

```yaml
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - DB_TYPE=postgresdb
      - DB_POSTGRESDB_HOST=postgres        # your existing service name
      - DB_POSTGRESDB_DATABASE=n8n          # separate DB from the trading data
      - DB_POSTGRESDB_USER=${N8N_DB_USER}
      - DB_POSTGRESDB_PASSWORD=${N8N_DB_PASSWORD}
      - N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}
      - GENERIC_TIMEZONE=UTC
      - TZ=UTC
    volumes:
      - n8n_data:/home/node/.n8n
    depends_on:
      - postgres
```

Give n8n its **own database/schema** for its internal state, but point its Postgres *node* (the one that writes news) at your trading database so it can upsert into `NewsEvent`. Run UTC everywhere — your `scheduledAt` comparisons in `riskEngine.isNewsWindow()` assume it.

---

## 3. Recommended data sources

Mapped to your `NewsEvent` schema:

```prisma
model NewsEvent {
  title       String
  impact      Impact   // LOW | MEDIUM | HIGH
  currency    String
  scheduledAt DateTime
  actual      String?
  forecast    String?
  previous    String?
  aiSummary   String?
}
```

### 3a. Economic calendar (the priority — feeds the risk gate)

Honest caveat: **there is no perfect free, ToS-clean, JSON economic-calendar API.** The options each have a catch:

| Source | Fields | Cost | Catch |
|---|---|---|---|
| **Forex Factory weekly XML** (`ff_calendar_thisweek.xml`) | title, impact, currency, time, forecast, previous | Free | Widely used, but FF's ToS restricts automated access — a **gray area**; use cautiously / low-frequency. ([FF notices](https://www.forexfactory.com/notices)) |
| **Trading Economics API** | full calendar, 150+ countries, forecast/previous/actual | Paid (free trial) | Best data; commercial pricing. ([TE calendar API](https://docs.tradingeconomics.com/)) |
| **Finnhub economic calendar** | date, currency, impact, actual/forecast/previous | Free tier exists but **calendar is now largely a premium endpoint** | Verify your plan covers it. ([Finnhub calendar](https://finnhub.io/docs/api/economic-calendar), [econ pricing](https://finnhub.io/pricing-economic-data-api)) |
| **Financial Modeling Prep** | event, country, actual/previous | ~$100/mo | No free calendar tier. ([FMP](https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar)) |
| **FRED API** | US series (CPI, NFP, rates) | Free, 120 req/min | Not an *event calendar* — historical indicator values; good for `actual` backfill. ([FRED](https://fred.stlouisfed.org/docs/api/fred/overview.html)) |

**Pragmatic path:** start with the **Forex Factory weekly XML** for the MVP (it carries impact/currency/forecast/previous directly and is the de-facto retail standard), while being aware of the ToS gray area and keeping polling gentle (e.g. a few times per day, not per minute). If/when this goes beyond personal/experimental use, **budget for Trading Economics** (cleanest licensing + actual-on-release). Use **FRED** to fill in `actual` values for US releases for free.

### 3b. Central-bank releases (free, official, no rate limits)

Direct RSS — free, real-time, no ToS issues. These catch rate decisions and statements that move FX hardest:

- **Fed** monetary-policy feed: `https://www.federalreserve.gov/feeds/press_monetary.xml` ([Fed feeds](https://www.federalreserve.gov/feeds/feeds.htm))
- **ECB** RSS: ([ECB RSS](https://www.ecb.europa.eu/home/html/rss.en.html))
- **BoJ** what's-new: `https://www.boj.or.jp/en/rss/whatsnew.xml` ([BoJ](https://www.boj.or.jp/en/whatsnew/index.htm))

### 3c. Breaking news for AI summaries

- **Marketaux** — best free tier: 100 req/day, sentiment + entity tags, 5,000+ sources. ([Marketaux pricing](https://www.marketaux.com/pricing))
- **Alpha Vantage NEWS_SENTIMENT** — free 25 req/day, includes sentiment scores. ([AV docs](https://www.alphavantage.co/documentation/#news-sentiment))
- **Free RSS** as zero-cost fallback: DailyFX, ForexLive, Reuters. ([DailyFX RSS](https://www.dailyfx.com/rss), [ForexLive RSS](https://www.forexlive.com/rss/))

Avoid NewsAPI.org for production (free tier is dev-only/localhost; Business is ~$449/mo) and GNews free tier (non-commercial only).

### Recommended starting stack (free / near-free)

**Calendar:** Forex Factory weekly XML (MVP) → Trading Economics (production). **Central banks:** Fed + ECB + BoJ RSS. **Headlines:** Marketaux free + central-bank RSS. Total cost at MVP: **$0**, with a clear paid upgrade path for the calendar when it matters.

---

## 4. Workflow design

### Workflow A — Economic calendar → `NewsEvent`

```
Schedule Trigger (every 6h, or 2–3×/day)
   → HTTP Request  (GET Forex Factory weekly XML  /  or Trading Economics calendar JSON)
   → Code (JS)     (parse XML→JSON; normalize each event)
   → Code (JS)     (map impact text → Impact enum; derive scheduledAt as UTC ISO; build deterministic dedupe key)
   → Filter        (keep only events with a usable currency + future-or-recent scheduledAt)
   → Remove Duplicates  (by dedupe key: currency|title|scheduledAt)
   → Postgres (Upsert into "NewsEvent")
```

Key normalization rules (do these in the Code node so the data matches your schema and risk engine exactly):

- **`impact` → enum.** Map source labels to `LOW | MEDIUM | HIGH`. Forex Factory uses color/“High/Medium/Low”; collapse anything ambiguous to `MEDIUM`. The risk engine only acts on `HIGH` (`isNewsWindow` filters `impact === "HIGH"`), so getting HIGH right is what counts.
- **`scheduledAt` → UTC `DateTime`.** Convert the source's local time to UTC ISO. This is critical: `riskEngine.isNewsWindow()` compares `scheduledAt` against `now` in a ±30-minute window. Wrong timezone = trades blocked at the wrong moments.
- **`currency`** as the ISO code (USD, EUR, JPY…) — matches how signals reason about pairs.
- **`forecast` / `previous` / `actual`** stored as strings (your schema allows null strings); leave `actual` null until release, then a later run updates it.
- **Idempotent upsert.** Use a deterministic key so re-runs don't duplicate — mirror the hashing approach already in `services/data/strategy_detector.py`. Add a `@@unique([currency, title, scheduledAt])` to `NewsEvent` (small migration) so the Postgres node can `ON CONFLICT DO UPDATE` and refresh `actual`/`forecast` cleanly.

### Workflow B — Breaking news → AI summary → `NewsEvent` / context

```
Schedule Trigger (every 15–30 min)
   → HTTP Request (Marketaux headlines)  +  RSS Read (Fed/ECB/BoJ, DailyFX)   [parallel branches]
   → Merge
   → Remove Duplicates (by article URL/title)
   → Filter (keep FX-relevant: matches currency keywords / central-bank terms)
   → Anthropic (Claude): "Summarize and classify impact (LOW/MEDIUM/HIGH) + affected currency, return JSON"
   → Code (parse structured JSON)
   → Postgres (insert summarized item; populate aiSummary, impact, currency, scheduledAt=publishedAt)
```

This gives the dashboard's market-context card (the Phase 3 plan §3 item) real material, and lets Claude pre-tag impact for headlines that aren't on the scheduled calendar (e.g. surprise central-bank remarks).

### Optional — push high-impact alerts to your backend

When Workflow A ingests a HIGH-impact event inside the next hour, add a branch: **HTTP Request → POST to your API** (`apps/api`) so the system can proactively widen the news-avoidance window or surface a banner, rather than waiting for the next signal-generation cycle to notice. n8n's Webhook/HTTP nodes make this trivial. ([webhook node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/))

---

## 5. How this plugs into the existing system

The wiring already exists on the consumption side — you only need the data to start flowing:

- `signalGenerator.ts` already does `prisma.newsEvent.findMany({ where: { scheduledAt: { gt: now } } })` and passes events to `validateTrade()`.
- `riskEngine.isNewsWindow()` already blocks trades when a `HIGH`-impact event is within 30 minutes (`NEWS_DEFAULT_BEFORE_MIN/AFTER_MIN`).
- `services/ai` already has `/analyze/market-context` that can consume news.

So once `NewsEvent` has rows, **the risk gate starts working automatically** — no trading-code changes required for Workflow A. That makes calendar ingestion the highest-value, lowest-integration-risk piece. Build it first.

---

## 6. Implementation steps

1. **Schema:** add `@@unique([currency, title, scheduledAt])` to `NewsEvent` in `prisma/schema.prisma`; migrate. (Enables clean upserts.)
2. **Infra:** add the `n8n` service to `docker-compose.yml` (its own Postgres DB for internal state; `N8N_ENCRYPTION_KEY` + creds via `.env`, never hardcoded). `docker compose up -d n8n`, open `http://localhost:5678`.
3. **Workflow A (calendar):** build the chain in §4; point the Postgres node at the trading DB; set Schedule to a few times daily. Verify `SELECT count(*) FROM "NewsEvent"` goes non-zero and `scheduledAt` values are correct UTC.
4. **Validate the risk gate:** confirm `signalGenerator.ts` now logs `risk_rejected: Inside news window` around a known HIGH-impact event.
5. **Workflow B (news + AI):** add headline sources + the Anthropic node; populate `aiSummary`.
6. **Optional alert branch** to `apps/api`.
7. **Export the workflows as JSON** and commit them (e.g. `infra/n8n/workflows/`) so they're version-controlled, not trapped in the n8n UI.

**Effort:** Workflow A ≈ half a day; Workflow B ≈ half a day; infra + schema ≈ 1–2 hours.

---

## 7. Caveats

- **Re-verify pricing/free-tiers before relying on them** — all figures are June 2026 and these providers change terms often. Finnhub's economic calendar in particular has drifted toward premium.
- **Respect Terms of Service.** Forex Factory and Investing.com calendar *scraping* is a ToS gray area; prefer their published feeds, keep polling gentle, and move to a licensed API (Trading Economics) for anything beyond experimental use. Central-bank RSS and the official news APIs are clean.
- **Timezone correctness is the #1 bug risk** here — everything UTC, end to end, because the risk engine does time-window math on `scheduledAt`.
- **Don't over-build.** Start with calendar-only ingestion (Workflow A). It's what the risk engine actually consumes today; headlines/AI are enhancement.

---

## Sources

**n8n**
- n8n pricing — https://n8n.io/pricing/
- Sustainable Use License — https://docs.n8n.io/sustainable-use-license/
- Docker hosting — https://docs.n8n.io/hosting/installation/docker/
- Schedule Trigger — https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/
- Core nodes — https://docs.n8n.io/integrations/builtin/core-nodes/
- Remove Duplicates — https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.removeduplicates/
- Anthropic node — https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-langchain.anthropic/
- Webhook node — https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
- Pricing cross-check (2026) — https://www.lowcode.agency/blog/n8n-pricing

**Economic calendar**
- Trading Economics API — https://docs.tradingeconomics.com/
- Finnhub economic calendar — https://finnhub.io/docs/api/economic-calendar · pricing — https://finnhub.io/pricing-economic-data-api
- Financial Modeling Prep — https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar
- FRED API — https://fred.stlouisfed.org/docs/api/fred/overview.html
- Forex Factory notices/ToS — https://www.forexfactory.com/notices

**Central banks**
- Federal Reserve feeds — https://www.federalreserve.gov/feeds/feeds.htm
- ECB RSS — https://www.ecb.europa.eu/home/html/rss.en.html
- Bank of Japan — https://www.boj.or.jp/en/whatsnew/index.htm

**News / sentiment**
- Marketaux pricing — https://www.marketaux.com/pricing
- Alpha Vantage NEWS_SENTIMENT — https://www.alphavantage.co/documentation/#news-sentiment
- NewsAPI pricing — https://newsapi.org/pricing
- DailyFX RSS — https://www.dailyfx.com/rss
- ForexLive RSS — https://www.forexlive.com/rss/
