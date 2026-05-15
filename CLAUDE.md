# AI Trading Intelligence Platform

## Stack
- Next.js 14 App Router (apps/web) — dashboard UI
- Node.js + Express + TypeScript (apps/api) — main backend
- Python + FastAPI (services/ai) — AI analysis service
- Python workers (services/data) — data ingestion
- PostgreSQL + TimescaleDB — time-series candle storage
- Redis — real-time cache and job queues
- Docker Compose — local dev environment

## Commands
- `docker-compose up -d` — start all services
- `cd apps/web && npm run dev` — start Next.js
- `cd apps/api && npm run dev` — start API server
- `cd services/ai && uvicorn main:app --reload` — start AI service

## Rules (NEVER break these)
- Risk engine must be called before any trade execution
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
