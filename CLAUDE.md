# AI Trading Intelligence Platform

## Stack
Two application frameworks (see docs/plans/11-nextjs-python-consolidation.md):
- Next.js 14 App Router (apps/web) — dashboard UI + a thin same-origin /api BFF
- Python + FastAPI (services/backend) — THE trading domain: signal gate, risk
  engine, execution, AI, Telegram/broker integrations, realtime, scheduled jobs
- Python workers (services/data) — ingestion, indicators, strategies, backtests
- Python + FastAPI (services/mt5bridge) — Windows/MT5 terminal adapter
- PostgreSQL + TimescaleDB — time-series candle storage
- Redis — cache, pub/sub, distributed locks
- Docker Compose — local dev environment

apps/api (Express + Prisma) is LEGACY: the rollback path only, behind the
`legacy` compose profile. Do not add features there.

## Commands
- `docker compose up -d` — start all services (web, backend, worker, n8n)
- `cd apps/web && npm run dev` — start Next.js (needs PYTHON_API_URL)
- `cd services/backend && .venv/bin/uvicorn app.main:app --reload` — start the backend
- `cd services/backend && .venv/bin/python -m pytest tests/ -q` — backend tests
- `cd services/backend && .venv/bin/alembic upgrade head` — migrate
- `docker compose --profile legacy up` — add Express for a rollback

## Rules (NEVER break these)
- Risk engine must be called before any trade execution
  (services/backend/app/domain/risk/engine.py, via app/domain/signals/gate.py)
- Alembic owns migrations; its baseline ADOPTS the Prisma schema, never recreates it
- The browser calls only same-origin /api/* — no backend URL or secret in the
  client bundle, and never a NEXT_PUBLIC_ prefix on a credential
- Never hardcode API keys — always use .env
- Every trade signal must be journaled with reasoning
- Backtest every strategy before live use
- Paper trade before real money

## Domain Glossary
- candle: OHLCV price bar (open, high, low, close, volume)
- signal: detected trade opportunity from strategy engine
- RR: risk/reward ratio (e.g. 1:2 means risk $1 to make $2)
- ATR: average true range, measures volatility
- EMA: exponential moving average, shows trend direction
- RSI: relative strength index, momentum oscillator 0-100
