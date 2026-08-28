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
  api/        LEGACY Express + Prisma API — rollback path only, not started
              by default (docker compose --profile legacy)
services/
  backend/    Python + FastAPI — the trading domain: signal gate, risk engine,
              execution, AI, Telegram/broker integrations, realtime, jobs
  data/       Python workers — ingestion, indicators, strategies, backtests
  ai/         Superseded: this code now lives in
              services/backend/app/integrations/ai
  mt5bridge/  Python + FastAPI — Windows/MT5 terminal adapter
packages/
  shared/     Shared TypeScript types
```

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
docker compose up                    # Next.js + Python (the current architecture)
docker compose --profile live up     # ...plus the MT5 bridge (live execution)
docker compose --profile legacy up   # ...plus Express, for a rollback
```

Never run both execution engines at once — see
`docs/runbooks/11-cutover-and-rollback.md`.

## Tests

```bash
cd services/backend && .venv/bin/python -m pytest tests/ -q   # backend
cd apps/web && npx tsc --noEmit                                # web typecheck
npm run test --workspace=@ai-trading/api                       # legacy Express
services/backend/.venv/bin/python services/backend/scripts/parity_check.py
```

For the Python services, see each service's own README.
