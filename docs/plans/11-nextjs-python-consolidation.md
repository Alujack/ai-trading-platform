# Next.js + Python Consolidation Plan

**Status:** Proposed — planning only  
**Date:** 2026-08-28  
**Decision owner:** Platform owner  
**Applies to:** `apps/web`, `apps/api`, `services/ai`, `services/data`, `services/mt5bridge`, Docker Compose, PostgreSQL, and Redis

## 1. Executive decision

Consolidate the application onto two application frameworks:

1. **Next.js** for the dashboard, authentication/session boundary, and a thin same-origin backend-for-frontend (BFF).
2. **Python with FastAPI** for all trading-domain APIs, AI orchestration, database access, Redis, schedulers, signal validation, risk enforcement, paper/live execution, Telegram, and broker integration.

The current backend is **Express, not NestJS**. The migration target is therefore Express/TypeScript → FastAPI/Python.

Do not move the trading engine into Next.js route handlers. Next.js is suitable for short request/response work, but risk-critical schedulers, reconciliation loops, broker sessions, approval expiry, and trailing-stop management need a durable Python process with an explicit lifecycle.

Next.js should expose same-origin `/api/*` endpoints only as a thin BFF. It must not own trading decisions, write trades directly, or contain a second risk engine.

## 2. Target architecture

```text
Browser
   │
   ▼
Next.js (`apps/web`)
   ├── UI and server-rendered pages
   ├── authentication/session handling
   └── thin `/api/*` BFF proxy
          │ server-only `PYTHON_API_URL`
          ▼
FastAPI backend (`services/backend`)
   ├── public and internal REST APIs
   ├── signal gate and risk engine
   ├── paper/live execution
   ├── AI provider orchestration
   ├── Telegram and broker integrations
   ├── realtime SSE
   └── scheduled/reconciliation jobs
          │
          ├── PostgreSQL / TimescaleDB
          ├── Redis
          └── MT5 FastAPI bridge

Python worker (`services/data`)
   ├── market-data ingestion
   ├── indicators and strategies
   ├── backtests
   └── submits candidates to FastAPI
```

### Runtime services after migration

| Service | Technology | Responsibility |
|---|---|---|
| `web` | Next.js | UI, session/auth boundary, BFF proxy |
| `backend` | Python + FastAPI | Domain API, risk, execution, AI, jobs, realtime |
| `worker` | Python | Ingestion, indicators, strategies, backtests |
| `mt5bridge` | Python + FastAPI | Windows/MT5 terminal adapter |
| `postgres` | PostgreSQL + TimescaleDB | Durable state |
| `redis` | Redis | Cache, pub/sub, distributed locks |

`n8n` is not an application framework, but it is another runtime. If “only Next.js and Python” is strict at the operational level, migrate its two news workflows into Python jobs in the final optional phase.

## 3. Ownership boundaries

### Next.js owns

- Dashboard routes, components, and presentation state.
- User authentication and session cookies when authentication is added.
- CSRF protection and request-level authorization.
- Same-origin API proxying to FastAPI.
- Browser-friendly caching only for safe read endpoints.
- No database credentials, broker credentials, LLM keys, or Redis access.

### FastAPI owns

- All PostgreSQL and Redis access.
- Every domain write and transaction.
- Signal candidate validation and idempotency.
- The single authoritative risk engine.
- Execution mode resolution, kill switch, circuit breakers, and portfolio caps.
- Paper trading and live MT5 execution.
- Trade journaling and AI trade review.
- Background schedules, reconciliation, approval expiry, and trailing stops.
- AI provider selection and AI calls.
- Telegram webhooks and broker credential encryption.
- SSE events and internal worker callbacks.

### Python worker owns

- Candle ingestion and indicator calculation.
- Strategy evaluation and candidate construction.
- Backtesting and research jobs.
- It may submit candidates, but it must never create a `Signal` or `Trade` directly.

## 4. Non-negotiable safety invariants

These rules must remain true during every migration phase:

1. A trade cannot open unless the authoritative risk engine approved it.
2. Signal candidates remain idempotent; retries cannot create duplicate signals or trades.
3. Only one runtime may execute trades. Shadow services are read-only and must not dual-write.
4. Every accepted signal stores its strategy reasoning and AI decision.
5. Every closed trade receives a journal entry and review state.
6. `OFF` and circuit-breaker states override `AUTO` and `CONFIRM`.
7. Broker passwords remain encrypted at rest and are never returned by an API.
8. Telegram callbacks remain authenticated and allowlisted.
9. Strategy backtests and paper-trading gates remain required before live promotion.
10. During risk/execution cutover, force the global execution mode to `CONFIRM` or `OFF`; never migrate while live `AUTO` execution is enabled.

## 5. Current system inventory

### Express API surface to migrate

| Area | Current endpoints | Risk level | Target order |
|---|---|---:|---:|
| Health | `/health`, `/api/health` | Low | 1 |
| Market reads | candles, symbols, news | Low | 1 |
| Trading reads | signals, positions, performance, journal | Medium | 2 |
| AI | provider configuration, market context | Medium | 2 |
| Backtests | list, detail, run, run status | Medium | 3 |
| Runtime config | risk, execution mode, kill, arm | High | 4 |
| Realtime | SSE stream, internal notifications | Medium | 4 |
| Telegram | settings, test, webhook registration, callbacks | High | 5 |
| Broker | encrypted credentials and session test | Critical | 5 |
| Signal gate | `/api/signals/candidate` | Critical | 6 |
| Execution | paper/live open, close, reconciliation, trailing stops | Critical | 6 |

### Background work to migrate

- Paper-trading scheduler.
- Approval expiry and pending-signal reconciliation.
- Trailing-stop manager.
- Scalp manager.
- Daily briefing.
- Weekly journal review and recommendation expiry.
- Broker startup-session check.
- Backtest child-process/job status handling.

### Persistence to migrate

The Prisma schema currently owns tables for candles, indicators, news, strategies, signals, trades, journals, risk logs, runtime risk configuration, execution settings, audits, backtests, broker credentials, approvals, and agent recommendations.

Python should take ownership through:

- **SQLAlchemy 2 async** for typed persistence and transactions.
- **Alembic** for migrations.
- **asyncpg** as the PostgreSQL driver.
- **redis-py asyncio** for cache, locks, and pub/sub.
- Pydantic response models that preserve the existing camelCase wire contract during migration.

## 6. Proposed Python backend structure

```text
services/backend/
  pyproject.toml
  Dockerfile
  alembic.ini
  migrations/
  app/
    main.py
    api/
      dependencies.py
      errors.py
      routes/
        health.py
        candles.py
        signals.py
        positions.py
        performance.py
        journal.py
        news.py
        market_context.py
        providers.py
        config.py
        backtests.py
        telegram.py
        brokers.py
        realtime.py
    core/
      settings.py
      logging.py
      security.py
    db/
      session.py
      models.py
      repositories/
    domain/
      risk/
      signals/
      execution/
      journal/
      performance/
    integrations/
      ai/
      telegram/
      mt5/
    jobs/
      scheduler.py
      reconciliation.py
      approvals.py
      trailing_stops.py
      reviews.py
```

Merge the existing `services/ai/src` code into `services/backend/app/integrations/ai` rather than maintaining a separate AI HTTP hop. Keep the existing `/analyze/*` routes temporarily for n8n and compatibility, then make internal backend calls invoke the Python functions directly.

## 7. Contract strategy

The API contract must not drift while the implementation language changes.

1. Capture representative JSON/status-code snapshots from every existing endpoint.
2. Define equivalent Pydantic request and response models.
3. Preserve current camelCase fields, enum values, pagination, decimal strings, timestamps, and error shape.
4. Generate an OpenAPI document from FastAPI in CI.
5. Generate or validate TypeScript client types from that OpenAPI document.
6. Keep a contract test that sends the same fixture request to Express and FastAPI and compares normalized responses.

Important compatibility details:

- Candle and signal decimal fields are currently serialized as strings.
- Timestamps are UTC ISO-8601 strings.
- Error responses used by the web app expect at least `{ "error": "..." }`.
- SSE must preserve `hello`, event data, heartbeat, reconnect, and no-buffering behavior.
- Provider and settings endpoints must never expose raw secrets.

## 8. Migration phases

### Phase 0 — Baseline and freeze

**Goal:** Create a reliable reference before changing runtime ownership.

- Freeze schema and endpoint additions during the migration.
- Record the current endpoint inventory and background-job inventory.
- Export OpenAPI-like fixtures or JSON snapshots for all routes.
- Add database fixtures covering wins, losses, open trades, approvals, news windows, and backtests.
- Run and archive current TypeScript tests for risk, performance, paper trading, broker mapping, and scalp management.
- Add end-to-end smoke tests for the dashboard’s current API calls.
- Confirm the current global execution mode is `CONFIRM` or `OFF`.

**Exit gate:** Every route and critical job has a baseline test or an explicitly documented gap.

### Phase 1 — FastAPI foundation

**Goal:** Create the new backend without routing production traffic to it.

- Create `services/backend` and move/copy the existing AI FastAPI code into it.
- Add structured settings, logging, health checks, SQLAlchemy, Alembic, asyncpg, and Redis.
- Convert the current Prisma schema into SQLAlchemy models without changing table or column names.
- Create an Alembic baseline that marks the existing schema as current; do not recreate production tables.
- Add transaction helpers and repository interfaces.
- Add `/health/live` and `/health/ready`.
- Run the backend beside Express on a different internal port.

**Exit gate:** Backend starts, connects to PostgreSQL/Redis, and passes readiness checks without modifying trading data.

### Phase 2 — Read-only API parity

**Goal:** Migrate low-risk reads first.

Implement and parity-test:

- Candles and indicators.
- Symbols.
- News.
- Signals list/detail.
- Positions and account summary.
- Performance metrics.
- Journal.
- Backtest list/detail.

Run both APIs against the same read-only fixture database and compare normalized responses.

**Exit gate:** Response bodies and status codes match for fixtures, empty states, filters, limits, offsets, and not-found cases.

### Phase 3 — AI and market context

**Goal:** Remove the internal Node → FastAPI AI proxy hop.

- Move provider state/key handling under the new backend.
- Port provider get/set/key/test APIs.
- Port market-context assembly and Redis caching.
- Call Python AI functions directly inside the backend.
- Preserve `/analyze/*` compatibility routes for external callers.
- Verify runtime provider switching and secret redaction.

**Exit gate:** Dashboard AI settings and market briefing work through FastAPI with no Express dependency.

### Phase 4 — Backtests, configuration, and realtime

**Goal:** Migrate medium/high-risk operational controls before execution.

- Move backtest process launching into a Python job manager using `asyncio.create_subprocess_exec`.
- Port risk-config resolution: `SYMBOL → STRATEGY → GLOBAL → defaults`.
- Port hard bounds and audit writes.
- Port execution mode, kill switch, and arm endpoints.
- Port Redis cache invalidation.
- Port SSE and internal realtime notifications.
- Use Redis locks for singleton jobs and configuration writes where needed.

**Exit gate:** Config parity tests pass, every mutation creates an audit row, kill/arm works, and SSE reconnects correctly.

### Phase 5 — Telegram and broker integrations

**Goal:** Port secrets and external callbacks without changing execution yet.

- Port Telegram override storage, secret validation, allowlists, commands, webhook registration, and callback handling.
- Port approval/recommendation decisions in a transaction.
- Port AES-256-GCM broker credential encryption with a compatibility test that decrypts existing ciphertext.
- Port broker status/save/test endpoints.
- Implement the MT5 client against the existing FastAPI bridge contract.
- Ensure logs redact tokens, passwords, login payloads, and API keys.

**Exit gate:** Existing encrypted credentials remain usable, unauthorized callbacks are rejected, and no secret appears in API responses or logs.

### Phase 6 — Risk gate and execution engine

**Goal:** Migrate the critical path with decision parity before ownership changes.

Port in this order:

1. Position sizing and risk/reward validation.
2. Two-sided high-impact news window.
3. Daily loss, daily profit target, and max-drawdown breakers.
4. Gold concurrency, direction, session-loss, and session-risk guards.
5. Portfolio open-trade, open-risk, and per-currency caps.
6. Signal candidate idempotency and cooldown.
7. AI score and explicit approval gate.
8. Execution decision: `OFF`, `CONFIRM`, `AUTO`.
9. Paper trade open/close management.
10. Live broker open/close management.
11. Approval expiry, pending-signal reconciliation, and trailing stops.
12. Journal creation and AI trade review after close.

#### Shadow-mode procedure

- Express remains the only writer/executor.
- FastAPI receives a copy of candidates with `shadow=true`.
- FastAPI computes decisions but cannot insert signals, open trades, call Telegram, or contact MT5.
- Store comparison records separately or emit structured parity logs.
- Compare approval, position size, reasons, score threshold, execution mode, and portfolio-cap result.
- Investigate every mismatch; do not accept “close enough” for risk decisions.

#### Critical cutover procedure

- Set global execution mode to `OFF` or `CONFIRM`.
- Stop candidate submission briefly.
- Confirm no in-flight backtest, approval callback, or broker order.
- Acquire a Redis migration lock.
- Switch `STRATEGY_GATE_URL` once, from Express to FastAPI.
- Ensure Express’s execution schedulers are stopped before FastAPI jobs start.
- Submit one idempotent paper candidate and verify the complete audit trail.
- Resume strategy submission in paper mode.

**Exit gate:** Zero unexplained shadow mismatches over the agreed validation window, all critical tests pass, and paper trading completes full open→manage→close→journal cycles.

### Phase 7 — Next.js BFF cutover

**Goal:** Give the browser one same-origin API while keeping domain logic in Python.

- Add thin Next.js route handlers that forward method, path, query, body, and approved headers to FastAPI.
- Set a server-only `PYTHON_API_URL`; never expose the Docker backend hostname to the browser.
- Change web requests from `NEXT_PUBLIC_API_URL` to relative `/api/*` calls.
- Do not place Prisma, Redis, risk, execution, or broker code in Next.js.
- Configure streaming pass-through for SSE without buffering.
- Apply authentication/authorization at the BFF and repeat authorization in FastAPI for sensitive writes.
- Cut over endpoint groups using route-level feature flags, reads first and critical writes last.

**Exit gate:** The browser makes no cross-origin API calls and contains no backend service URL or secret.

### Phase 8 — Remove Express and Prisma runtime

**Goal:** Complete the two-framework architecture.

- Stop and remove the `api` service from Docker Compose.
- Delete Express route/middleware/bootstrap code after an archival tag or branch is created.
- Remove Express, CORS, Helmet, Morgan, node-cron, Prisma runtime/client, and API-only TypeScript dependencies.
- Move Prisma SQL migration history into the migration archive; Alembic becomes authoritative.
- Remove `npm run dev:api` and update root scripts.
- Rename `services/ai` to `services/backend` if not already done.
- Update `README.md`, `docs/ARCHITECTURE.md`, diagrams, environment examples, and operational runbooks.
- Verify that only Next.js and Python application containers remain.

**Exit gate:** `docker compose up` starts without the Express image/service, repository searches find no active Express imports, and all platform smoke tests pass.

### Phase 9 — Optional n8n removal

**Goal:** Make the operational runtime strictly Next.js + Python.

- Port calendar ingestion and news summarization workflows to Python scheduled jobs.
- Preserve news deduplication and `NewsEvent` unique constraints.
- Add provider retry/backoff, rate limits, and dead-letter logging.
- Remove the n8n database, container, workflows, and encryption key after data export.

**Exit gate:** News ingestion remains healthy for the agreed observation period without n8n.

## 9. Test matrix

### Contract tests

- Success, empty, validation-error, not-found, dependency-down, and timeout cases.
- Exact status codes, field names, decimal representation, timestamps, pagination, and error structure.
- SSE hello/event/heartbeat/disconnect/reconnect behavior.

### Risk tests

- Position size at valid and invalid boundaries.
- Exact minimum risk/reward tolerance.
- Daily loss equality versus greater-than behavior.
- Profit-target circuit breaker.
- Max drawdown.
- High-impact event before/after windows.
- Gold concurrency/direction/session limits.
- Max trades per day and max open trades per strategy.
- Portfolio open-risk and per-currency caps.
- RiskLog persistence even when a candidate is rejected.

### Execution tests

- Candidate idempotency race.
- Cooldown.
- AI low score and explicit `approved=false`.
- `OFF`, `CONFIRM`, and `AUTO` behavior.
- Approval expiry and repeated button taps.
- Paper trade open, stop, target, trailing stop, and close.
- Broker unit-to-lot conversion and symbol mapping.
- Broker timeout/retry without duplicate orders.
- Journal and trade review after close.

### Operational tests

- Cold PostgreSQL/Redis start and reconnect.
- One scheduler owner under multiple FastAPI workers.
- Graceful shutdown with no half-finished trade transaction.
- Redis outage degrades caching/realtime without bypassing risk.
- AI outage fails closed for signal generation.
- MT5 outage fails closed and preserves the signal/audit trail.

## 10. Deployment and rollback strategy

### Deployment sequence

1. Deploy FastAPI in read-only/shadow mode.
2. Cut over read endpoints through Next.js one group at a time.
3. Cut over config and integration endpoints.
4. Validate critical decision shadow parity.
5. Cut over candidate submission in paper mode.
6. Run full paper-trading observation period.
7. Disable Express schedulers, then remove Express traffic.
8. Remove Express only after rollback is no longer needed.

### Immediate rollback triggers

- Any risk decision mismatch.
- Duplicate signal, approval, trade, or broker order.
- Missing journal/audit entry.
- Incorrect position size.
- Kill switch or breaker not taking precedence.
- SSE failure that hides execution state during cutover.
- Database migration inconsistency.
- Broker or Telegram secrets exposed in logs/responses.

### Rollback procedure

- Set global execution mode `OFF`.
- Stop candidate submission.
- Stop FastAPI execution jobs.
- Point the worker gate back to Express.
- Restart exactly one Express scheduler owner.
- Verify pending approvals and open trades before re-arming.
- Never run both execution engines concurrently during rollback.

## 11. Environment-variable changes

### Add

- `PYTHON_API_URL=http://backend:8000` — server-only Next.js BFF target.
- `BACKEND_HOST`, `BACKEND_PORT`.
- `BACKEND_JOB_OWNER=true|false` — enables scheduled jobs on exactly one process.
- `API_SHADOW_MODE=true|false`.
- `API_MIGRATION_LOCK_KEY`.

### Remove after cutover

- `NEXT_PUBLIC_API_URL` — browser should use same-origin `/api`.
- `API_HOST`, `API_PORT`, and `API_PUBLIC_URL` for Express.
- `AI_SERVICE_URL` once AI functions are in-process in the Python backend.
- Node-specific scheduler flags after equivalent Python settings exist.

Secrets stay server-only. No variable containing broker, Telegram, database, Redis, or LLM credentials may use the `NEXT_PUBLIC_` prefix.

## 12. Recommended pull-request sequence

Keep each change independently reviewable and reversible:

1. Baseline fixtures and contract harness.
2. FastAPI backend skeleton, database, Redis, readiness.
3. SQLAlchemy models and Alembic baseline.
4. Read-only routes.
5. AI/provider/market-context consolidation.
6. Config, backtests, and realtime.
7. Telegram and broker integrations.
8. Python risk engine with translated unit tests.
9. Signal gate shadow mode and parity reporting.
10. Execution, schedulers, and paper-trading soak.
11. Next.js BFF and same-origin browser cutover.
12. Express/Prisma runtime removal and documentation.
13. Optional n8n-to-Python migration.

Do not combine the risk-engine port, live broker cutover, and Express deletion in one pull request.

## 13. Definition of done

The consolidation is complete only when:

- The running application uses Next.js and Python/FastAPI only.
- No Express/Nest server is started or required.
- The browser calls only same-origin Next.js endpoints.
- Next.js has no database, Redis, broker, Telegram, or LLM secrets.
- FastAPI is the sole owner of domain state and trade execution.
- Python jobs are singleton-safe and observable.
- The risk engine is called before every paper or live execution path.
- Contract, risk, execution, integration, and operational tests pass.
- Paper trading has completed the agreed soak period with no parity defects.
- Rollback documentation has been exercised at least once in a non-production environment.
- README, architecture diagrams, Compose, environment examples, and runbooks match the new architecture.

## 14. Recommendation

Proceed with the FastAPI-domain architecture and a thin Next.js BFF. Do not use Next.js as the trading engine or scheduler host. This produces the clearest ownership model, reuses the platform’s existing Python strategy/backtest/AI investment, removes the Express service cleanly, and keeps risk-critical work in one durable backend runtime.

