# n8n — News Automation Layer

Self-hosted n8n that feeds the trading DB's `NewsEvent` table. Replaces the old
Python news fetcher (`services/data/news_fetcher.py`). See the full plan in
[`docs/plans/06-n8n-migration-buildplan.md`](../../docs/plans/06-n8n-migration-buildplan.md).

```
infra/n8n/
  workflows/
    workflow-a-calendar.json    # ForexFactory economic calendar → NewsEvent  (feeds the risk gate)
    workflow-b-news-ai.json     # Alpha Vantage + Fed RSS → AI summary → NewsEvent
```

## 1. Start n8n

It's a service in the root `docker-compose.yml`. It needs an encryption key first:

```bash
# generate once, put in your .env (gitignored)
echo "N8N_ENCRYPTION_KEY=$(openssl rand -hex 24)" >> .env

docker compose up -d postgres n8n
open http://localhost:5678        # create the owner account on first run
```

n8n keeps its **own** state in a separate `n8n` database (auto-created by
`infra/postgres/init/01-n8n-db.sql` on first Postgres init). If your Postgres
volume already existed, create it once:

```bash
docker compose exec postgres psql -U postgres -c "CREATE DATABASE n8n;"
```

## 2. Add the trading-DB credential (required before workflows run)

The workflows write `NewsEvent` into the **trading** database via a Postgres
credential named **`Trading DB`**. The committed workflow JSON pins its
credential id to **`tradingdbcred0001`**, so if you create the credential with
that id (CLI path below) the nodes link automatically.

**UI path:**

1. n8n → **Credentials → New → Postgres**
2. Host `postgres` · Port `5432` · Database `trading` · User/Password from your `.env`
   (host/port are the *internal* Docker network values, not the host's 55432)
3. Save, name it exactly **`Trading DB`**, and **Test connection**.
4. Open each workflow's Postgres node once and select this credential.

**CLI path (no clicks, pins the id):** put a decrypted creds file like
`[{"id":"tradingdbcred0001","name":"Trading DB","type":"postgres","data":{"host":"postgres","port":5432,"database":"trading","user":"postgres","password":"postgres","sslMode":"disable"}}]`
in the container and run:

```bash
docker compose exec n8n n8n import:credentials --input=/tmp/n8n-creds.json
```

## 3. Import the workflows

**UI path:** n8n → **Workflows → Import from File** → pick each JSON in
`workflows/`, select the `Trading DB` credential on the Postgres node, toggle
**Active**.

**CLI path:** copy the JSON into the container and import (each file has a
top-level `id`, required by the CLI):

```bash
docker compose cp infra/n8n/workflows/workflow-a-calendar.json n8n:/tmp/wf-a.json
docker compose exec n8n n8n import:workflow --input=/tmp/wf-a.json
```

> CLI `n8n execute` can't start a *schedule*-triggered workflow (it needs a
> manual trigger). To do a one-off test, use **Test workflow** in the UI, or
> activate the workflow and let the schedule fire.

## 4. Environment the workflows expect

Set these on the **n8n container** (add to the `n8n` service `environment:` in
`docker-compose.yml`, or n8n's own env):

| Var | Used by | Notes |
|---|---|---|
| `ALPHA_VANTAGE_API_KEY` | Workflow B → Alpha Vantage node | Free tier is **25 req/day** — see caveat below. Omit and the AV branch no-ops (RSS still runs). |
| `NEWS_SUMMARY_URL` | Workflow B → AI node | Defaults to `http://host.docker.internal:8000/analyze/news-summary`. Point at the AI service. |

The AI summary call hits our `services/ai` endpoint, so it **respects the
Mock / Claude / Gemini toggle** in the dashboard — no model key lives in n8n.

## 5. Verify

```bash
# Workflow A: run once manually in the UI, then
docker compose exec postgres psql -U postgres -d trading \
  -c "SELECT impact, count(*) FROM \"NewsEvent\" GROUP BY impact;"
# expect a non-zero HIGH count; spot-check a known release is correct UTC.

# second manual run must add ZERO duplicates (upsert on title+scheduledAt).
```

Once rows are flowing, the risk engine's news blackout works automatically —
`riskEngine.isNewsWindow()` already consumes these rows. **No trading-code
change is needed.** Only after this is verified do you remove the Python path
(build plan Milestone 4).

## Design notes

- **Postgres writes use `executeQuery`** with a Code-built, escaped
  `INSERT ... ON CONFLICT DO UPDATE`. This is portable across n8n versions and
  refreshes `actual`/`forecast` when a release prints.
- **All timestamps are naive UTC** (`YYYY-MM-DD HH:MM:SS`) to match the
  `TIMESTAMP WITHOUT TIME ZONE` columns and the risk engine's UTC window math.
  This is the #1 correctness risk — don't change it.
- **Deterministic ids** (`ff_<hash>`, `news_<hash>`) keep re-runs idempotent.
- **Edit in the UI, then re-export** the JSON back into `workflows/` and commit,
  so the source of truth stays in git and not trapped in the n8n database.

## Caveats

- **Alpha Vantage free tier ≈ 25 req/day.** The 30-min schedule in Workflow B
  will exhaust it. Widen the interval, gate to market hours, or upgrade. The
  `onError: continueRegularOutput` on that node means a rate-limit response
  won't kill the run — the Fed RSS branch still produces summaries.
- **ForexFactory ToS** is a gray area for automated access; the 6-hour cadence
  in Workflow A is deliberately gentle. Move to a licensed calendar (Trading
  Economics) for anything beyond experimental use. See `docs/plans/05-…`.
- **Secrets never go in the committed JSON.** n8n exports reference credentials
  by name/id, not value — confirm before committing any re-export.
