# Runbook — execution cutover and rollback (plan 11)

**Applies to:** the handover of trade execution between the legacy Express API
(`apps/api`) and the Python backend (`services/backend`).

**The one rule:** never run both execution engines at once. Everything below
exists to make that impossible rather than merely unlikely.

---

## 0. Where things stand

`docker compose up` starts the two-framework architecture: Next.js (`web`) +
Python (`backend`, `worker`) + Postgres + Redis + n8n. Express is **not started**
— it lives behind the `legacy` profile purely as the rollback path:

```bash
docker compose up                     # current architecture
docker compose --profile legacy up    # ...plus Express, for a rollback
docker compose --profile live up      # ...plus the MT5 bridge
```

The switch that decides which runtime gates trades is a single env var read by
the strategy runner:

| Variable | Cutover value | Rollback value |
|---|---|---|
| `STRATEGY_GATE_URL` | `http://backend:8000/api/signals/candidate` | `http://api:4000/api/signals/candidate` |
| `WORKER_API_BASE` | `http://backend:8000` | `http://api:4000` |

---

## 1. Pre-cutover checks

Run these and read the output — do not proceed on assumption.

```bash
# 1. Both runtimes agree on every API response.
docker compose --profile legacy up -d postgres redis backend api
services/backend/.venv/bin/python services/backend/scripts/parity_check.py
#    -> must print "N/N cases match"

# 2. The backend's schema matches the database.
cd services/backend && .venv/bin/alembic revision --autogenerate -m "drift check"
#    -> the generated upgrade() must be `pass`; then DELETE the file
cd services/backend && .venv/bin/python -m pytest tests/ -q
#    -> all green

# 3. The global execution mode is CONFIRM or OFF. NEVER cut over under AUTO.
curl -s localhost:8000/api/config/execution
```

**Invariant 10 of the plan:** during risk/execution cutover the global execution
mode must be `CONFIRM` or `OFF`. If it reads `AUTO`, stop and set it:

```bash
curl -sX POST localhost:8000/api/config/kill -H 'content-type: application/json' \
  -d '{"reason":"pre-cutover"}'
```

---

## 2. Shadow validation (before ownership changes)

Run a second backend instance with `API_SHADOW_MODE=true`. It computes the full
decision — idempotency, cooldown, AI score, the risk engine, position sizing —
and **writes nothing**: no `Signal`, no `Trade`, no `RiskLog`, no Telegram
message, no MT5 order, and its schedulers stay stopped.

```bash
cd services/backend
API_SHADOW_MODE=true .venv/bin/uvicorn app.main:app --port 8101
```

Post the same candidate to both gates and compare. The shadow response carries
the decision explicitly:

```json
{
  "status": "rejected",
  "reason": "shadow_mode: decision computed, no write",
  "score": 78,
  "shadow": {
    "wouldGenerate": true, "score": 78, "positionSize": 0.2,
    "riskApproved": true, "reasons": [], "minScore": 70
  }
}
```

Compare **approval, position size, reasons, score threshold, execution mode and
portfolio-cap result**. Investigate every mismatch; "close enough" is not
acceptable for a risk decision. The risk engine also emits a
`[risk] SHADOW_riskLog {...}` line per evaluation for log-based comparison.

---

## 3. Cutover

```bash
# 1. Freeze execution.
curl -sX POST localhost:8000/api/config/kill -H 'content-type: application/json' \
  -d '{"reason":"cutover"}'

# 2. Stop candidate submission.
docker compose stop worker

# 3. Confirm nothing is in flight.
curl -s localhost:8000/api/backtests/run/status     # running must be false
curl -s localhost:8000/api/positions                # note openCount
docker exec trading-postgres psql -U postgres -d trading -tAc \
  'SELECT count(*) FROM "Approval" WHERE status = '"'"'PENDING'"'"';'
#    -> resolve or let expire before continuing

# 4. Ensure Express owns nothing. Its container already sets
#    ENABLE_PAPER_TRADING/WEEKLY_REVIEW/DAILY_BRIEFING=false, so simply:
docker compose stop api

# 5. Point the worker at the FastAPI gate (already the compose default) and
#    confirm the backend owns the jobs.
curl -s localhost:8000/health/ready | python3 -m json.tool
#    -> jobs.jobOwner must be true, jobs.shadowMode false

# 6. Restart submission in paper mode and re-arm to CONFIRM.
docker compose up -d worker
curl -sX POST localhost:8000/api/config/arm -H 'content-type: application/json' -d '{}'

# 7. Submit one idempotent paper candidate and verify the whole audit trail.
curl -sX POST localhost:8000/api/signals/candidate \
  -H 'content-type: application/json' \
  -d '{"strategyName":"cutover_probe","symbol":"XAUUSD","timeframe":"60min",
       "direction":"LONG","entryPrice":4000,"stopLoss":3995,"takeProfit":4010,
       "confidence":70,"reasoning":"cutover verification",
       "clientId":"cutover-probe-1"}'
# Re-POST the identical body: the second call MUST return
#   {"status":"skipped","reason":"idempotent_duplicate"}
```

Then confirm the trail landed:

```sql
SELECT id, status, "strategyName" FROM "Signal" ORDER BY "createdAt" DESC LIMIT 1;
SELECT count(*) FROM "RiskLog"  WHERE "createdAt" > now() - interval '5 minutes';
SELECT actor, entity, scope FROM "ConfigAudit" ORDER BY "createdAt" DESC LIMIT 5;
```

---

## 4. Soak

Leave the desk in **paper** mode (`BROKER=paper`) and CONFIRM for the agreed
observation window. What to watch each day:

- `GET /health/ready` — `db`, `redis`, and `jobs.running`.
- One journal entry per closed trade, with a grade and an R-multiple.
- One `RiskLog` row per evaluated candidate, approved or not.
- A `ConfigAudit` row for every config/mode change.
- No duplicate `Signal` for a repeated `clientId`, and no duplicate `Trade` per
  `Signal`.

---

## 5. Immediate rollback triggers

Roll back on **any** of these, without deliberation:

- A risk decision mismatch.
- A duplicate signal, approval, trade or broker order.
- A missing journal or audit entry.
- An incorrect position size.
- A kill switch or breaker that did not take precedence.
- An SSE failure that hides execution state during cutover.
- A database migration inconsistency.
- A broker or Telegram secret appearing in a log or an API response.

---

## 6. Rollback procedure

```bash
# 1. Freeze execution FIRST.
curl -sX POST localhost:8000/api/config/kill -H 'content-type: application/json' \
  -d '{"reason":"rollback"}'

# 2. Stop candidate submission.
docker compose stop worker

# 3. Stop the FastAPI execution jobs (leave the API up for reads).
#    Either stop the container, or restart it as a non-owner:
docker compose stop backend
#    ...or: BACKEND_JOB_OWNER=false docker compose up -d backend

# 4. Point the worker's gate back at Express, and start exactly ONE Express
#    scheduler owner.
STRATEGY_GATE_URL=http://api:4000/api/signals/candidate \
WORKER_API_BASE=http://api:4000 \
ENABLE_PAPER_TRADING=true \
  docker compose --profile legacy up -d api worker

# 5. Verify pending approvals and open trades BEFORE re-arming.
curl -s localhost:4000/api/positions
docker exec trading-postgres psql -U postgres -d trading -tAc \
  'SELECT id, status FROM "Approval" WHERE status = '"'"'PENDING'"'"';'

# 6. Re-arm only once the above reads clean.
curl -sX POST localhost:4000/api/config/arm -H 'content-type: application/json' -d '{}'
```

**Never** have both `backend` (with `BACKEND_JOB_OWNER=true`) and `api` (with
`ENABLE_PAPER_TRADING=true`) running at the same time.

### Why the schema survives a rollback

Alembic's baseline revision *adopts* the Prisma schema rather than recreating it,
and revision `20260828000002` creates `RawSignal`/`FeatureFlag` idempotently. The
Prisma migration history stays in `apps/api/prisma/migrations` and
`_prisma_migrations` is left untouched, so Express can still run
`prisma migrate deploy` against the same database after a rollback.

---

## 7. Exercising this runbook (required before it counts)

The plan's definition of done requires the rollback to have been exercised at
least once in a non-production environment. Do it against a scratch database so
nothing real is at stake:

```bash
docker exec trading-postgres psql -U postgres -c 'CREATE DATABASE trading_rehearsal;'
cd services/backend && \
  DATABASE_URL=postgresql://postgres:postgres@localhost:55432/trading_rehearsal \
  .venv/bin/alembic upgrade head
# Point both runtimes at trading_rehearsal, then walk sections 3 and 6 end to end.
```

Record the date and the outcome here when done:

| Date | Environment | Outcome | Notes |
|---|---|---|---|
| _pending_ | | | Rehearsal not yet run — see §7 |
