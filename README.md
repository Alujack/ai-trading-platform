# AI Trading Platform

Monorepo for an AI-powered trading intelligence platform. Two application
frameworks: **Next.js** for the dashboard and a thin same-origin BFF, **Python /
FastAPI** for every trading-domain concern (see
`docs/plans/11-nextjs-python-consolidation.md`).

## Layout

```
apps/
  web/        Next.js 14 (App Router, TypeScript, Tailwind)
              + app/api/[...path] — the same-origin BFF proxy
services/
  backend/    Python + FastAPI — the trading domain: signal gate, risk engine,
              execution, AI, Telegram/broker integrations, realtime, jobs
  data/       Python workers — ingestion, indicators, strategies, backtests
  mt5bridge/  Python + FastAPI — Windows/MT5 terminal adapter
packages/
  shared/     Shared TypeScript types (used by apps/web)
```

The Express API (`apps/api`) and the standalone AI service (`services/ai`) were
removed once the FastAPI backend took over — see
`docs/plans/11-nextjs-python-consolidation.md`. The Express implementation is
recoverable from the `archive/express-pre-plan11` tag, and its Prisma schema and
migration history are archived at `docs/archive/prisma/`.

### Request path

```
Browser → Next.js (same-origin /api/*) → FastAPI backend → Postgres / Redis / MT5
```

The browser makes no cross-origin API calls and holds no backend URL or secret:
`PYTHON_API_URL` is server-only.

## Getting started

```bash
cp .env.example .env
npm install
docker compose up -d               # postgres, redis, backend, web, worker, n8n
open http://localhost:3100
```

The backend runs `alembic upgrade head` on start. Against an existing database
the baseline revision *adopts* the schema Prisma created without touching a row.

### Running pieces individually

```bash
docker compose up -d postgres redis

cd services/backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a && . ../../.env && set +a
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000

cd apps/web && PYTHON_API_URL=http://localhost:8000 npm run dev
```

## Compose profiles

```bash
docker compose up                    # Next.js + Python (the whole platform)
docker compose --profile live up     # ...plus the MT5 bridge (live execution)
```

Operations, schema changes and rollback: `docs/runbooks/11-cutover-and-rollback.md`.

## Tests

```bash
cd services/backend && .venv/bin/python -m pytest tests/ -q   # backend (251 tests)
cd services/backend && .venv/bin/ruff check app tests          # backend lint
cd apps/web && npx tsc --noEmit                                # web typecheck
```

The DB-backed tests (schema drift, full paper cycle) need Postgres:

```bash
POSTGRES_PORT=55432 sh services/backend/scripts/setup_test_db.sh
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/trading_test
cd services/backend && .venv/bin/python -m pytest tests/ -q
```

For the Python services, see each service's own README.
