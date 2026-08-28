# AI Trading Platform — System Architecture & Code Review

> **⚠️ HISTORICAL SNAPSHOT — superseded on 2026-08-28.**
>
> This document describes the architecture *before* the Next.js + Python
> consolidation (`docs/plans/11-nextjs-python-consolidation.md`). It still shows
> an Express API at `:4000` as the main backend and a separate AI service at
> `services/ai`, neither of which is how the platform runs now:
>
> - The trading domain moved to **`services/backend`** (FastAPI).
> - **`services/ai` has been deleted**; its code is `services/backend/app/integrations/ai`.
> - **`apps/api`** is the legacy rollback path only, behind the `legacy` compose profile.
> - The browser calls **same-origin `/api/*`** on Next.js, which proxies to the backend.
>
> For the current architecture see **`docs/ARCHITECTURE.md`**. The code-review
> findings below are kept as a record of what was true at the time.

## System Architecture Diagram

```mermaid
graph TB
    subgraph External["☁️ External Services"]
        LLM["LLM Providers<br/>Anthropic / Gemini / Mock"]
        TD["Twelve Data API<br/>Market Data"]
        AV["Alpha Vantage API<br/>News Headlines"]
        MT5T["MetaTrader 5<br/>Terminal (Windows)"]
        TG["Telegram Bot API<br/>Alerts & Approvals"]
    end

    subgraph Frontend["🖥️ Frontend Layer"]
        WEB["<b>Web Dashboard</b><br/>Next.js 14 App Router<br/>:3000 → host :3100<br/><i>apps/web</i>"]
    end

    subgraph Backend["⚙️ Backend Layer"]
        API["<b>API Server</b><br/>Express + TypeScript + Prisma<br/>:4000<br/><i>apps/api</i>"]
    end

    subgraph Intelligence["🧠 Intelligence Layer"]
        AI["<b>AI Analysis Service</b><br/>Python FastAPI<br/>:8000<br/><i>services/ai</i>"]
    end

    subgraph Workers["🔄 Worker Layer"]
        DW["<b>Data Worker</b><br/>Python Async<br/><i>services/data</i>"]
        N8N["<b>n8n Automation</b><br/>News Ingestion<br/>:5678<br/><i>infra/n8n</i>"]
    end

    subgraph Bridge["🔌 Broker Bridge (live profile)"]
        MT5B["<b>MT5 Bridge</b><br/>FastAPI + Wine<br/>:8800<br/><i>services/mt5bridge</i>"]
    end

    subgraph Storage["🗄️ Data Layer"]
        PG[("PostgreSQL<br/>+ TimescaleDB<br/>:5432")]
        RD[("Redis<br/>Cache + Queues<br/>:6379")]
    end

    subgraph SharedPkg["📦 Shared"]
        SH["<b>packages/shared</b><br/>TypeScript Types"]
    end

    %% Frontend → Backend
    WEB -- "REST API<br/>(NEXT_PUBLIC_API_URL)<br/>SWR polling" --> API
    WEB -- "SSE<br/>/api/stream" --> API

    %% API → Storage
    API -- "Prisma ORM" --> PG
    API -- "ioredis" --> RD

    %% API → AI
    API -- "POST /analyze/*<br/>validate-signal<br/>market-context<br/>journal-review<br/>trade-review" --> AI

    %% API → MT5 Bridge
    API -- "POST /order, /close<br/>GET /positions, /account<br/>X-Bridge-Token auth" --> MT5B

    %% API → Telegram
    API -- "Send alerts<br/>Approval requests" --> TG
    TG -- "Webhook callbacks<br/>/api/telegram/webhook" --> API

    %% AI → LLM
    AI -- "Inference calls" --> LLM

    %% Data Worker → Storage
    DW -- "asyncpg<br/>upsert candles + indicators" --> PG
    DW -- "POST /api/signals/candidate<br/>(strategy runner)" --> API

    %% Data Worker → External
    DW -- "Fetch OHLCV" --> TD

    %% n8n → Services
    N8N -- "POST /analyze/news-summary" --> AI
    N8N -- "Fetch headlines" --> AV
    N8N -- "Upsert NewsEvent" --> PG

    %% MT5 Bridge → Terminal
    MT5B -- "MetaTrader5 Python lib" --> MT5T

    %% Shared Types
    SH -.-> API
    SH -.-> WEB

    %% Styling
    classDef frontend fill:#3b82f6,stroke:#1e40af,color:#fff
    classDef backend fill:#8b5cf6,stroke:#5b21b6,color:#fff
    classDef ai fill:#ec4899,stroke:#9d174d,color:#fff
    classDef worker fill:#f59e0b,stroke:#b45309,color:#fff
    classDef storage fill:#10b981,stroke:#065f46,color:#fff
    classDef external fill:#6b7280,stroke:#374151,color:#fff
    classDef bridge fill:#ef4444,stroke:#991b1b,color:#fff
    classDef shared fill:#6366f1,stroke:#4338ca,color:#fff

    class WEB frontend
    class API backend
    class AI ai
    class DW,N8N worker
    class PG,RD storage
    class LLM,TD,AV,MT5T,TG external
    class MT5B bridge
    class SH shared
```

---

## Detailed Data Flow Diagram

```mermaid
sequenceDiagram
    participant TD as Twelve Data
    participant DW as Data Worker
    participant DB as PostgreSQL
    participant SR as Strategy Runner
    participant API as API Server
    participant AI as AI Service
    participant RE as Risk Engine
    participant EP as Execution Policy
    participant TG as Telegram
    participant BR as MT5 Bridge
    participant WEB as Dashboard

    Note over DW,DB: 1. Candle Ingestion (scheduled per-timeframe loops)
    DW->>TD: Fetch OHLCV (XAUUSD, EURUSD, BTCUSD)
    TD-->>DW: Candle bars
    DW->>DB: Upsert candles
    DW->>DW: Calculate indicators (RSI, EMA20/50/200, ATR)
    DW->>DB: Upsert indicators
    DW->>API: POST /api/internal/rt-notify (SSE push)

    Note over SR,API: 2. Strategy Scan (every 15 min)
    SR->>DB: Load enabled strategies
    SR->>DB: Load BarWindow (candles + indicators)
    SR->>SR: Compute regime (TRENDING/RANGING/VOLATILE)
    SR->>SR: Run strategy.evaluate()
    SR->>API: POST /api/signals/candidate

    Note over API,RE: 3. Signal Gate (gate.ts)
    API->>DB: Check idempotency + cooldown
    API->>DB: Load candles, indicators, news
    API->>AI: POST /analyze/validate-signal
    AI-->>API: {score, approved, reasoning, concerns}
    API->>RE: validateTrade()
    RE->>DB: Persist RiskLog
    RE-->>API: {approved, positionSize, reasons}
    API->>DB: Create Signal (PENDING)
    API->>API: SSE publish "signal" event

    Note over EP,BR: 4. Execution (OFF / AUTO / CONFIRM)
    API->>EP: decideExecution(signal)
    alt AUTO mode
        EP->>BR: POST /order (if BROKER=exness)
        BR-->>EP: {ticket, fillPrice}
        EP->>DB: Create Trade
    else CONFIRM mode
        EP->>TG: Send approval request
        TG-->>EP: User approves/rejects
        EP->>BR: POST /order (on approval)
    else OFF mode
        EP->>EP: Hold (signal logged, not traded)
    end

    Note over API,WEB: 5. Dashboard (continuous)
    WEB->>API: GET /api/candles, /signals, /performance
    API-->>WEB: JSON responses
    WEB->>API: SSE /api/stream (realtime updates)
```

---

## Component Architecture

```mermaid
graph LR
    subgraph APIAPP["API Server (apps/api/src)"]
        direction TB
        IDX["index.ts<br/>Bootstrap + schedulers"]
        APP["app.ts<br/>Express setup"]
        
        subgraph Routes["Routes (/api)"]
            R1["candles"]
            R2["signals + candidate"]
            R3["performance"]
            R4["positions"]
            R5["config + risk"]
            R6["brokers"]
            R7["backtests"]
            R8["telegram webhook + config"]
            R9["marketContext"]
            R10["stream (SSE)"]
            R11["journal"]
            R12["news + newsAlert"]
            R13["aiProvider"]
            R14["symbols"]
            R15["health"]
        end

        subgraph Core["Core Modules"]
            GATE["signals/gate.ts<br/>Signal validation gate"]
            RISK["risk/riskEngine.ts<br/>Position sizing, daily loss,<br/>drawdown, RR, news window"]
            EXEC["execution/executionPolicy.ts<br/>OFF/AUTO/CONFIRM routing"]
            PAPER["execution/paperTrading.ts<br/>Paper trade sim"]
            LIVE["execution/liveTrade.ts<br/>Live trade execution"]
            SCALP["execution/scalpManager.ts<br/>15s scalp management"]
            SCALPD["execution/scalpDecision.ts<br/>Scalp exit decider"]
            SCHED["execution/scheduler.ts<br/>Cron orchestration"]
            BRIEF["execution/dailyBriefing.ts<br/>Daily summary routine"]
            NBRIEF["execution/newsBrief.ts<br/>Morning news brief"]
            BTRUN["execution/backtestRunner.ts<br/>Backtest execution"]
        end

        subgraph BrokerMod["Broker Abstraction"]
            BI["broker/index.ts<br/>Factory: paper vs exness"]
            PB["broker/paperBroker.ts"]
            EB["broker/exnessBroker.ts"]
        end

        subgraph TGMod["Telegram"]
            TGC["telegram/config.ts"]
            TGA["telegram/approvals.ts"]
            TGT["telegram/telegram.ts"]
        end

        subgraph Libs["Libraries"]
            PR["lib/prisma.ts"]
            RED["lib/redis.ts"]
            CRYPTO["lib/crypto.ts<br/>AES-256-GCM"]
            RT["lib/realtime.ts<br/>SSE broadcasting"]
        end
    end
```

---

## Database Schema (Prisma)

```mermaid
erDiagram
    Candle {
        string id PK
        string symbol
        string timeframe
        decimal open
        decimal high
        decimal low
        decimal close
        decimal volume
        datetime timestamp
    }

    Indicator {
        string id PK
        string symbol
        string timeframe
        datetime timestamp
        decimal rsi
        decimal ema20
        decimal ema50
        decimal ema200
        decimal atr
    }

    NewsEvent {
        string id PK
        string title
        enum impact
        string currency
        datetime scheduledAt
        string aiSummary
    }

    Strategy {
        string id PK
        string name UK
        boolean enabled
        string regimes
        json params
    }

    Signal {
        string id PK
        string symbol
        string timeframe
        enum direction
        decimal entryPrice
        decimal stopLoss
        decimal takeProfit
        int confidenceScore
        string aiReasoning
        string strategyName
        enum status
    }

    Trade {
        string id PK
        string signalId FK
        decimal entryPrice
        decimal exitPrice
        decimal positionSize
        decimal riskAmount
        decimal profitLoss
        enum status
        string externalOrderId
        string broker
    }

    Journal {
        string id PK
        string tradeId FK
        string notes
        string aiReview
        string grade
        string outcome
        string lesson
        decimal rMultiple
    }

    RiskLog {
        string id PK
        decimal accountBalance
        decimal riskPercent
        decimal positionSize
        decimal dailyLoss
        boolean circuitBreakerTripped
    }

    RiskConfig {
        string id PK
        string scope
        string scopeKey
        decimal riskPerTradePct
        decimal minRR
        decimal dailyLossLimitPct
        int maxOpenTrades
    }

    ExecutionSetting {
        string id PK
        string scope
        string scopeKey
        enum mode
    }

    BacktestRun {
        string id PK
        string label
        json config
        json results
        json equityCurves
    }

    BrokerCredential {
        string id PK
        string broker
        int login
        string passwordEnc
        string server
        string env
        boolean isActive
    }

    Approval {
        string id PK
        string signalId FK
        enum status
        string chatId
        datetime expiresAt
    }

    ConfigAudit {
        string id PK
        string actor
        string entity
        json before
        json after
    }

    Signal ||--o{ Trade : "generates"
    Trade ||--o{ Journal : "records"
    Signal ||--o| Approval : "requires"
```

---

## Docker Compose Service Map

```mermaid
graph TB
    subgraph Default["Default Profile (docker compose up)"]
        PG["postgres<br/>timescale/timescaledb:latest-pg16<br/>:5432"]
        RED["redis<br/>redis:7-alpine<br/>:6379"]
        AIS["ai<br/>services/ai<br/>:8000"]
        APIS["api<br/>Dockerfile.node<br/>:4000"]
        WEBS["web<br/>Dockerfile.node<br/>:3100→3000"]
        WORK["worker<br/>services/data"]
        N8NS["n8n<br/>docker.n8n.io/n8nio/n8n<br/>:5678"]
    end

    subgraph Live["Live Profile (--profile live)"]
        MT5["mt5bridge<br/>services/mt5bridge<br/>:8800 + :3001 (noVNC)"]
    end

    APIS --> PG
    APIS --> RED
    APIS --> AIS
    WEBS --> APIS
    WORK --> PG
    WORK --> APIS
    N8NS --> PG
    N8NS --> AIS
    MT5 -.-> APIS

    classDef infra fill:#10b981,stroke:#065f46,color:#fff
    classDef app fill:#3b82f6,stroke:#1e40af,color:#fff
    classDef live fill:#ef4444,stroke:#991b1b,color:#fff

    class PG,RED infra
    class AIS,APIS,WEBS,WORK,N8NS app
    class MT5 live
```

---

## Trading Strategies

| Strategy | File | Type | Regimes |
|----------|------|------|---------|
| `trend_ema` | [trend_ema.py](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data/strategies/trend_ema.py) | Trend following | TRENDING |
| `meanrev_rsi` | [meanrev_rsi.py](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data/strategies/meanrev_rsi.py) | Mean reversion | RANGING |
| `scalp_ema` | [scalp_ema.py](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data/strategies/scalp_ema.py) | Scalping | TRENDING |
| `scalp_vwap` | [scalp_vwap.py](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data/strategies/scalp_vwap.py) | VWAP scalping | TRENDING |
| ICT strategies | [strategies/ict/](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data/strategies/ict) | Price action | Various |

---

## Code Review

### ✅ Strengths

| Area | Details |
|------|---------|
| **Architecture** | Clean monorepo separation: `apps/`, `services/`, `packages/shared`. Each concern (ingestion, AI, execution, UI) is isolated in its own service |
| **Risk Discipline** | The `gateCandidate()` function enforces a **mandatory** AI validation → risk engine → execution policy pipeline. No bypass path exists |
| **Execution Modes** | Three-mode execution policy (OFF/AUTO/CONFIRM) with Telegram human-in-the-loop approval. Scoped at GLOBAL/STRATEGY/SYMBOL granularity |
| **Broker Abstraction** | Clean `PaperBroker` / `ExnessBroker` interface with symbol-map translation. Live profile behind Docker `--profile live` |
| **Idempotency** | Signal gate handles duplicate `clientId` and cooldown windows. MT5 bridge checks `clientTag` before opening duplicate positions |
| **Regime Gating** | Strategy runner classifies market regime (TRENDING/RANGING/VOLATILE) and gates strategies against their declared regimes |
| **Audit Trail** | `ConfigAudit` table records every config change with actor + before/after JSON diff. `RiskLog` persists every risk check |
| **Credential Security** | Broker passwords stored AES-256-GCM encrypted at rest ([crypto.ts](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/lib/crypto.ts)) |
| **Learning Loop** | Closed trades get AI-reviewed (`/analyze/trade-review`) with grade (A–F), R-multiple, and lesson — compounding discipline |

---

### ⚠️ Issues & Recommendations

#### 🔴 Critical

| # | Issue | Location | Recommendation |
|---|-------|----------|----------------|
| 1 | **Dead code after return** — `provider_test()` has a `return` statement on line 142 that is **unreachable** after the `return TestResult(...)` on line 141 | [main.py:141-142](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/ai/src/main.py#L141-L142) | Remove the dead `return ProviderState(...)` on line 142 |
| 2 | **No authentication on API** — The Express API has no auth middleware. All endpoints (`/api/signals`, `/api/config`, `/api/brokers`) are open. `JWT_SECRET` is defined in `.env.example` but never used | [app.ts](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/app.ts) | Add JWT or API key middleware, at minimum to mutation endpoints |
| 3 | **Paper account state from env vars** — Account balance/peak are read from `PAPER_ACCOUNT_BALANCE` env var, not tracked in DB. Risk calculations use stale values after trades run | [gate.ts:62-69](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/signals/gate.ts#L62-L69) | Derive running balance from `SUM(profitLoss)` on closed trades or maintain an `Account` table |
| 13 | **Duplicate `readAccountState()` / `readAccount()` functions** | [gate.ts:62](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/signals/gate.ts#L62) & [executionPolicy.ts:31](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/execution/executionPolicy.ts#L31) | Consolidate logic into a single shared helper inside a common module to prevent settings drift. |
| 14 | **`computeTodayLoss()` duplicate queries** | [gate.ts:71](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/signals/gate.ts#L71) & [executionPolicy.ts:43](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/execution/executionPolicy.ts#L43) | Consolidate into a single module to ensure the gate and circuit breaker use the same calculations. |
| 15 | **Circuit breaker drawdown uses static balance base** | [executionPolicy.ts:71-75](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/execution/executionPolicy.ts#L71-L75) | Real balance base updates are ignored, leading to circuit breaker calculation inaccuracies. |

#### 🟡 Important

| # | Issue | Location | Recommendation |
|---|-------|----------|----------------|
| 4 | **Threading lock in async context** — MT5 bridge uses `threading.Lock()` inside `async def` FastAPI endpoints. This blocks the event loop and limits throughput | [app.py:45](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/mt5bridge/app.py#L45) | Use `asyncio.Lock` or `run_in_executor` to avoid blocking the async event loop |
| 5 | **No rate limiting** — The API has no rate limiting. Since it accepts external candidates via `POST /api/signals/candidate`, this is an open attack surface | [routes/signals.routes.ts](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/routes/signals.routes.ts) | Add `express-rate-limit` middleware |
| 6 | **Global mutable state for MT5 creds** — `session_login()` mutates module-level `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` via `global` keyword. Not safe across concurrent requests | [app.py:177](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/mt5bridge/app.py#L177) | Wrap in a dataclass; the lock already serializes, but explicit state is cleaner |
| 7 | **Twelve Data rate limit risk** — The worker exceeds the 800 req/day free tier after ~13 hours, as the code itself notes. Continuous running will start failing silently | [main.py:29-31](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data/main.py#L29-L31) | Add explicit daily request counting + backoff, or upgrade API tier |
| 8 | **No graceful shutdown in data worker** — `main.py` runs `asyncio.gather()` but has no signal handler for SIGTERM. Docker sends SIGTERM on `compose down`, so the worker gets killed mid-write | [main.py:126-151](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data/main.py#L126-L151) | Add a signal handler to cancel the gather and run `close_pool()` cleanly |
| 16 | **No health check on data worker** | [docker-compose.yml:138-155](file:///Users/microstore/CambotixSolutions/ai-trading-platform/docker-compose.yml#L138-L155) | Add a standard healthcheck option to ensure auto-restarts on network drop or fatal loop failure. |
| 17 | **`_ensure_connected()` locks on every call** | [app.py:51-67](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/mt5bridge/app.py#L51-L67) | Run checking in a background thread or connection pool loop, rather than blocking the serialize lock on every inbound endpoint check. |
| 18 | **`maxTradesPerDay` missing from `RISK_FIELDS` resolver** | [resolve.ts:21-33](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/config/resolve.ts#L21-L33) | Add the parameter to `RISK_FIELDS` so scope overrides (symbol, strategy) can actually take effect. |
| 19 | **Reconciler sweep does not limit signal age** | [executionPolicy.ts:196](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/execution/executionPolicy.ts#L196) | Add a timeframe limit (e.g. `createdAt > now - TTL`) to check only relevant pending signals. |

#### 🟢 Minor / Polish

| # | Issue | Location | Recommendation |
|---|-------|----------|----------------|
| 9 | **`.env` committed to git** | Root & service directories | **[RESOLVED/FALSE POSITIVE]** Already ignored in `.gitignore`, no files are tracked. |
| 10 | **Missing TypeScript strict mode** | [tsconfig.base.json](file:///Users/microstore/CambotixSolutions/ai-trading-platform/tsconfig.base.json) | **[RESOLVED/FALSE POSITIVE]** already has `"strict": true` and `"noUncheckedIndexedAccess": true`. |
| 11 | **Backtest data modules scattered** — `backtester.py`, `backfill_history.py`, `prepare_backtest.py`, `walkforward.py`, `baseline_mc.py`, `frame_sweep.py` are all top-level in `services/data/` | [services/data/](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/data) | Move into a `services/data/backtest/` subpackage for clarity |
| 12 | **CORS wide open** — `cors()` with no options allows any origin. Fine for dev, risky in production | [app.ts:13](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/app.ts#L13) | Configure `origin` allowlist from env: `cors({ origin: process.env.CORS_ORIGIN })` |
| 20 | **In-function python imports** | [app.py:346](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/mt5bridge/app.py#L346) & [app.py:368](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/mt5bridge/app.py#L368) | Move standard imports (like `import time`) to the top level of the file. |
| 21 | **No request timeout on AI service call** | [gate.ts:170-173](file:///Users/microstore/CambotixSolutions/ai-trading-platform/apps/api/src/signals/gate.ts#L170-L173) | Supply an `AbortSignal.timeout(...)` to prevent indefinite blocking if the LLM/provider lags. |
| 22 | **Truncated SHA-1 modulo for MT5 magic number** | [app.py:80](file:///Users/microstore/CambotixSolutions/ai-trading-platform/services/mt5bridge/app.py#L80) | Keep an eye on potential tag collisions due to the `% 2_000_000_000` truncation. |

---

### 📊 Codebase Statistics

| Metric | Value |
|--------|-------|
| Languages | TypeScript, Python, SQL |
| Monorepo Workspaces | 2 apps (`web`, `api`) + 1 package (`shared`) |
| Docker Services | 7 (default) + 1 (live profile) |
| API Routes | 16 route modules |
| Prisma Models | 14 |
| Trading Strategies | 5+ (pluggable registry) |
| Scheduled Jobs | Paper trade (5m), Scalp (15s), Weekly review (Sun), Daily briefing (6:00 UTC), Approval expiry (1m) |
| External Integrations | Twelve Data, Alpha Vantage, Telegram, MetaTrader 5, LLM providers, n8n |
