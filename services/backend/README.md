# services/backend — trading-domain API (FastAPI)

The single owner of the trading domain: PostgreSQL and Redis access, the signal
gate, the authoritative risk engine, paper/live execution, AI orchestration,
Telegram and broker integrations, SSE, and the scheduled jobs.

Next.js sits in front of it as a thin same-origin BFF with **no** database,
broker, Telegram or LLM credentials. See `docs/plans/11-nextjs-python-consolidation.md`.

## Layout

```
app/
  main.py                    FastAPI app + lifespan (jobs, broker session, shutdown)
  api/
    errors.py                Express-compatible error shape ({ "error": ... })
    dependencies.py          Session dependency + the shared query enums
    routes/                  One module per endpoint group
  core/
    settings.py              Typed env contract (fails fast on a bad value)
    logging.py               Secret-redacting formatter
    security.py              AES-256-GCM, wire-compatible with lib/crypto.ts
    serialization.py         Decimal/date/number shapes that match the old JSON
    realtime.py              Redis pub/sub event fan-out
    ids.py                   Row ids (uuid4 hex, as the Python worker already writes)
  db/
    models.py                SQLAlchemy 2 models over the Prisma-created schema
    session.py               Async engine + transactional session scope
    redis_client.py          Cache, pub/sub and the singleton job locks
    enums.py                 The Postgres enum types
  domain/
    config/                  defaults, resolver (SYMBOL►STRATEGY►GLOBAL), store, flags
    risk/engine.py           THE risk engine — called before any execution
    signals/                 gate.py (AI + risk) and raw_feed.py (observe-only)
    execution/               policy, paper, live, scalp, trailing, backtests, agents
    performance/metrics.py   Win rate, expectancy, profit factor, drawdown
    market_context.py        Candles + indicators + news → AI briefing (cached)
  integrations/
    ai/                      The former services/ai, now in-process
    telegram/                Client, credential store, approvals
  jobs/
    scheduler.py             APScheduler jobs, single-owner + no-overlap
    clock.py                 The one answer to "what time is it" (always UTC)
migrations/                  Alembic; the baseline ADOPTS the Prisma schema
scripts/parity_check.py      Express-vs-FastAPI contract diff
tests/                       Risk, execution, contract, drift and cycle tests
```

## Running it

```bash
# In Compose (the normal way — runs `alembic upgrade head` first):
docker compose up -d backend

# Locally:
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a && . ../../.env && set +a
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000
```

## Tests

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests/ -q      # unit + contract (no DB needed)
.venv/bin/ruff check app tests

# The schema-drift and full-cycle tests need Postgres:
POSTGRES_PORT=55432 sh scripts/setup_test_db.sh     # creates + migrates trading_test
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/trading_test
.venv/bin/python -m pytest tests/ -q

# Contract parity against the legacy Express API (both must be running):
.venv/bin/python scripts/parity_check.py
```

## Things to know before changing this service

**The risk engine is called before every execution path.** `domain/signals/gate.py`
is the only place a `Signal` is created, and it calls `validate_trade` first.
`domain/execution/policy.py` runs *after* that and can only hold or route a
trade, never loosen a check.

**Migrations adopt, they don't recreate.** The baseline revision detects an
existing `Candle` table and no-ops, so running it against the production database
stamps the version without touching a row. On a fresh database the same revision
builds the schema from the models. `alembic revision --autogenerate` must produce
an empty diff — `tests/test_schema_drift.py` enforces that.

**The wire format is a contract.** Decimals serialize as strings in Decimal.js
`toFixed()` form (`2650.00000000` → `"2650"`), timestamps as
`Date.toISOString()`, and integral numbers without a decimal point (`1`, not
`1.0` — see `js_number`). `scripts/parity_check.py` diffs the live responses
against Express.

**Shadow mode writes nothing.** With `API_SHADOW_MODE=true` the full decision
runs and is reported, but no signal, trade, `RiskLog`, Telegram message or broker
order is written, and the schedulers stay stopped.

**One job owner.** `BACKEND_JOB_OWNER=true` on exactly one process; each tick also
takes a short Redis lock, so extra replicas cannot double-execute.

**Secrets never leave.** `core/logging.py` redacts them from logs; the broker
password is AES-256-GCM at rest and no endpoint returns it. Broker credentials
written by the old Express API still decrypt here, and vice versa
(`tests/test_security.py` checks both directions against real Node output).
