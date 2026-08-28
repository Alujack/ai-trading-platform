# Runbook — backend operations and rollback (plan 11)

**Status:** the cutover is complete. Express and Prisma were removed in Phase 8;
`services/backend` (FastAPI) is the only runtime that gates and executes trades.

**The one rule:** never run two execution engines at once. Since Phase 8 there is
only one, so the way to break this rule is to restore the archived Express API
and start its schedulers while the backend is still running. Don't.

---

## 0. What runs now

```bash
docker compose up                     # postgres, redis, backend, web, worker, n8n
docker compose --profile live up      # ...plus the MT5 bridge (live execution)
```

Only Next.js and Python application containers remain. The browser calls
same-origin `/api/*` on Next.js, which proxies to the backend over the
server-only `PYTHON_API_URL`.

| Concern | Owner |
|---|---|
| Signal gate, risk engine, execution, AI, Telegram, broker, SSE, jobs | `services/backend` |
| Ingestion, indicators, strategies, backtests | `services/data` |
| UI, session boundary, `/api/*` BFF | `apps/web` |

---

## 1. Daily health checks

```bash
curl -s localhost:8000/health/ready | python3 -m json.tool
```

Read three things:

* `db` and `redis` — both `connected`.
* `jobs.jobOwner` — must be `true` on exactly one process.
* `jobs.running` — `paperCron`, `approvalExpiry`, and the schedules you enabled.

Then confirm the AI provider actually works. The gate **fails closed**: a
rejecting or unreachable provider means every candidate is `skipped`, never
approved — safe, but silently idle.

```bash
curl -s localhost:8000/api/ai-provider | python3 -m json.tool
curl -sX POST localhost:8000/api/ai-provider/test \
  -H 'content-type: application/json' -d '{"provider":"gemini"}'
```

`{"ok": false, ...}` here means no signals are being generated. Rotate the key.

---

## 2. Freezing execution

```bash
# Panic: global mode OFF. Signals still generate and log; nothing opens.
curl -sX POST localhost:8000/api/config/kill \
  -H 'content-type: application/json' -d '{"reason":"why"}'

# Clear it (back to CONFIRM, not AUTO).
curl -sX POST localhost:8000/api/config/arm \
  -H 'content-type: application/json' -d '{}'
```

`OFF` and a tripped circuit breaker both override `AUTO` and `CONFIRM`. Every one
of these writes a `ConfigAudit` row naming the actor.

To stop candidates reaching the gate at all: `docker compose stop worker`.

---

## 3. Verifying the audit trail

After any change, the trail should be complete:

```sql
-- one RiskLog per evaluated candidate, approved or not
SELECT count(*) FROM "RiskLog" WHERE "createdAt" > now() - interval '1 hour';

-- one Journal per closed trade, with a grade and an R-multiple
SELECT t.id, t.status, j.outcome, j.grade, j."rMultiple"
FROM "Trade" t LEFT JOIN "Journal" j ON j."tradeId" = t.id
WHERE t.status = 'CLOSED' ORDER BY t."closedAt" DESC LIMIT 5;

-- every config/mode change, and who made it
SELECT actor, entity, scope, "scopeKey", "createdAt"
FROM "ConfigAudit" ORDER BY "createdAt" DESC LIMIT 10;
```

A closed trade without a journal row, or a candidate without a risk log, is a bug
— not a cosmetic gap.

---

## 4. Schema changes

Alembic is authoritative.

```bash
cd services/backend
.venv/bin/alembic upgrade head                      # apply
.venv/bin/alembic revision --autogenerate -m "..."  # after editing models.py
```

An autogenerate run on an unchanged codebase must produce an **empty** diff.
`tests/test_schema_drift.py` enforces that, so a model change without a migration
fails the build.

The baseline revision *adopts* the schema Prisma created (it detects a live
`Candle` table and no-ops), so it never replays history against production. The
Prisma schema and its twelve SQL migrations are archived at
`docs/archive/prisma/` as the record of how the schema came to be, and
`_prisma_migrations` is deliberately left in the database.

---

## 5. Rollback

Phase 8 removed the compose-profile rollback. What replaces it depends on what
broke.

### 5a. A bad backend deploy — roll the code back

The database schema has not changed, so this is a normal revert:

```bash
git revert <commit>            # or: git checkout <good-sha> -- services/backend
docker compose up -d --build backend
curl -s localhost:8000/health/ready
```

### 5b. A bad migration

```bash
cd services/backend
.venv/bin/alembic downgrade -1
```

The **baseline revision refuses to downgrade** on purpose — it adopts a
pre-existing schema, so reversing it would mean dropping the trade history.
Restore from a backup instead.

### 5c. Restoring the Express API (last resort)

Only if the FastAPI backend is unusable and you need the old engine back. It is
in the archive tag, not in the working tree:

```bash
git tag -l "archive/*"                                  # archive/express-pre-plan11
git checkout archive/express-pre-plan11 -- apps/api     # restore the source
git checkout archive/express-pre-plan11 -- Dockerfile.node package.json package-lock.json
npm install                                             # reinstall express/prisma/etc
npx prisma generate --schema=apps/api/prisma/schema.prisma
```

Then, **in this order**:

```bash
# 1. Freeze, and stop the new engine's jobs FIRST.
curl -sX POST localhost:8000/api/config/kill -H 'content-type: application/json' -d '{"reason":"rollback"}'
docker compose stop worker backend

# 2. Start Express (restore its compose service from the tag too, or run it on the host).
ENABLE_PAPER_TRADING=true API_PORT=4000 npx tsx apps/api/src/index.ts

# 3. Point the worker back at the Express gate.
STRATEGY_GATE_URL=http://api:4000/api/signals/candidate \
WORKER_API_BASE=http://api:4000 \
  docker compose up -d worker

# 4. Verify pending approvals and open trades BEFORE re-arming.
curl -s localhost:4000/api/positions
curl -sX POST localhost:4000/api/config/arm -H 'content-type: application/json' -d '{}'
```

Express reads the same database and the same encrypted broker credentials
(AES-256-GCM, verified compatible in both directions), and `prisma migrate deploy`
still works against it — `_prisma_migrations` was left intact for exactly this.

**Never** have the backend's schedulers (`BACKEND_JOB_OWNER=true`) and Express's
(`ENABLE_PAPER_TRADING=true`) running at the same time.

---

## 6. Contract parity against the archive

`services/backend/scripts/parity_check.py` diffs the backend's responses against
Express. Express is no longer in the tree, so using it now means restoring the
archive first (§5c) and running both. It is kept because it is the tool that
caught two real defects during the migration — a loosened concurrency cap and a
numeric wire-format drift — and it is the right instrument if a response shape is
ever questioned.

---

## 7. Immediate escalation triggers

Freeze execution (§2) on any of these:

- A duplicate signal, approval, trade or broker order.
- A missing journal or audit entry.
- An incorrect position size.
- A kill switch or breaker that did not take precedence.
- A broker or Telegram secret appearing in a log or an API response.
- Candles going stale while the worker reports healthy.
