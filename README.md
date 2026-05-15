# AI Trading Platform

Monorepo for an AI-powered trading intelligence platform.

## Layout

```
apps/
  web/        Next.js 14 (App Router, TypeScript, Tailwind)
  api/        Node.js + Express + TypeScript
services/
  data/       Python data ingestion workers
  ai/         Python FastAPI AI analysis service
packages/
  shared/     Shared TypeScript types
```

## Getting started

```bash
cp .env.example .env
npm install
npm run infra:up          # start Postgres (TimescaleDB) + Redis
npm run dev:api           # in one terminal
npm run dev:web           # in another
```

For the Python services, see each service's own README.
