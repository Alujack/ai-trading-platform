# AI Trading Platform — User Story & System / Signal-Flow Diagrams

**Prepared:** 2026-06-24
**Scope:** End-to-end documentation of how the platform turns raw price data into a journaled, risk-gated, semi-auto-executable trade signal — with the ICT Daily Signal Engine as the signal source.
**Audience:** engineering + product. Every box/arrow below maps to real code (file references inline).

> All Mermaid diagrams render on GitHub and in most Markdown viewers.

---

## 1. User Story

### Primary persona — *Sam, the semi-auto discretionary trader*

> **As a** discretionary FX trader who believes in ICT (liquidity / time / institutional intent) but can't watch the charts all day,
> **I want** the system to independently run each ICT strategy, combine them into **at most one high-conviction EURUSD signal per day**, explain its reasoning, and let me **approve execution with one click**,
> **so that** I only act on A+ setups, never break my own risk rules, and have a full journal to review — without staring at screens during the London/NY killzones.

### Acceptance criteria

| # | Given | When | Then |
|---|-------|------|------|
| 1 | EURUSD candles are flowing | a killzone is active and ≥2 ICT arrays stack in the bias direction inside discount | the engine emits **one** candidate with entry/SL/TP + a human-readable reason |
| 2 | a candidate is produced | it reaches the API gate | it is **AI-validated** *and* **risk-validated** (RR ≥ 2, daily-loss/drawdown/news checks) before it is ever persisted |
| 3 | a signal passes both gates | it is saved | every signal is **journaled with its full reasoning** (`aiReasoning`) — never silently |
| 4 | execution mode = `CONFIRM` | a signal is generated | Sam gets a **Telegram alert with Approve / Reject buttons**; tapping ✅ opens the (paper) trade |
| 5 | execution mode = `OFF` or a circuit breaker is tripped | a signal is generated | **no trade is opened** (the system taking zero trades is correct behaviour) |
| 6 | an open trade hits TP or SL | the 5-minute monitor runs | the trade auto-closes, P&L is recorded, and a **journal entry** is created |
| 7 | Sam opens the dashboard | at any time | he sees live KPIs, positions, signals, the risk engine state, and **backtest results** — updated in near-real-time via SSE |
| 8 | a new strategy is proposed | before it can trade live | it must be **backtested + walk-forward validated** (per `CLAUDE.md`) — surfaced on the `/backtests` page |

### Journey

```mermaid
journey
    title Sam's day with the ICT Signal Engine
    section Before open (passive)
      Worker ingests EURUSD candles -> TimescaleDB: 5: Worker
      Indicators + regime computed: 5: Worker
    section London / NY killzone
      ICT detectors scan each strategy: 4: Engine
      Confluence aggregator scores stacked arrays: 4: Engine
      One high-conviction candidate emitted: 5: Engine
    section Validation gate
      AI scores the setup: 4: AI
      Risk engine checks RR / loss / news: 5: Risk
      Signal journaled (PENDING): 5: API
    section Decision (one-click)
      Telegram alert with reasoning + buttons: 5: Sam
      Sam taps Approve: 5: Sam
      Paper trade opened: 4: Engine
    section After
      Trade auto-closes at TP/SL: 4: Engine
      Journal entry + weekly AI review: 5: Sam
```

---

## 2. System Architecture

Four services + Postgres/TimescaleDB + Redis. The web app (`:3100`) talks only to the API (`:4000`); the Python worker pushes candidates **into** the API gate and pings it for realtime fan-out.

```mermaid
flowchart TB
    subgraph EXT["External (free tiers)"]
        FEED["Data feed<br/>Twelve Data (candles)<br/>fetcher.py / SYMBOL_MAP"]
        TG["Telegram Bot API"]
        LLM["LLM providers<br/>Anthropic claude-sonnet-4-6<br/>Gemini 2.5 / mock"]
    end

    subgraph DATA["services/data — Python worker (main.py)"]
        ING["Ingest loops (per timeframe)<br/>fetch_candles -> upsert_candles<br/>calculate_indicators"]
        REG["regime.py<br/>TRENDING / RANGING / VOLATILE"]
        RUN["strategy_runner.run_once<br/>(every 15 min)"]
        subgraph STRAT["strategies/ (registry)"]
            ICTD["ICT detectors<br/>ict_sweep_mss / ict_order_block / ict_fvg"]
            ICTC["ict_confluence (aggregator)<br/>killzone + bias + premium/discount<br/>+ ≥2 arrays + RR≥2"]
            CLS["close-only<br/>trend_ema / meanrev_rsi / scalp_ema"]
        end
        BT["backtester.py / walkforward.py<br/>(replay -> BacktestRun)"]
    end

    subgraph AI["services/ai — FastAPI (:8000)"]
        VS["POST /analyze/validate-signal<br/>score 0-100 + approved + concerns"]
        JR["POST /analyze/journal-review"]
        MC["POST /analyze/market-context"]
    end

    subgraph API["apps/api — Express (:4000)"]
        GATE["Signal gate<br/>POST /api/signals/candidate<br/>signals/gate.ts"]
        RISK["Risk engine<br/>risk/riskEngine.ts (validateTrade)"]
        POL["Execution policy<br/>execution/executionPolicy.ts"]
        PAPER["Paper trading<br/>execution/paperTrading.ts"]
        SCHED["Schedulers<br/>paper */5 - expiry */1 - weekly Sun"]
        APV["Telegram approvals<br/>telegram/approvals.ts"]
        SSE["GET /api/stream (SSE)<br/>+ /api/internal/rt-notify"]
        BTR["Backtests API<br/>backtests.routes.ts (+ runner)"]
    end

    subgraph STORE["Data stores"]
        TSDB[("PostgreSQL + TimescaleDB<br/>Candle - Indicator - Signal<br/>Trade - Journal - RiskLog<br/>Approval - BacktestRun")]
        REDIS[("Redis<br/>cache / realtime")]
    end

    subgraph WEB["apps/web — Next.js (:3100)"]
        PAGES["/ /signals /trades<br/>/journal /risk /backtests"]
    end

    FEED --> ING --> TSDB
    ING --> REG
    RUN --> STRAT
    REG -. regime gate .-> RUN
    TSDB -- "OHLC + indicators window" --> RUN
    STRAT -- "SignalCandidate (camelCase JSON)" --> GATE
    BT --> TSDB

    GATE --> VS
    VS --> LLM
    GATE --> RISK
    RISK --> TSDB
    GATE --> TSDB
    GATE --> POL
    POL --> PAPER
    POL --> APV
    PAPER --> TSDB
    APV <--> TG
    SCHED --> PAPER
    SCHED --> APV
    SCHED --> JR
    JR --> LLM

    ING -- rt-notify --> SSE
    GATE -. rt-notify .-> SSE
    SSE --> PAGES
    PAGES -- "REST (SWR poll + SSE revalidate)" --> API
    BTR --> TSDB
    PAGES --> BTR
```

---

## 3. Signal Generation — Detailed Flow (the core question: *how does it get a signal?*)

This sequence is the heart of the system: from a freshly-closed candle to a **persisted, journaled `PENDING` signal**. Every step is real code.

```mermaid
sequenceDiagram
    autonumber
    participant Feed as Twelve Data
    participant W as Worker
    participant DB as TimescaleDB
    participant R as strategy_runner
    participant ICT as ict_confluence
    participant G as API Gate
    participant AI as services/ai
    participant RE as Risk engine
    participant SSE as SSE /api/stream

    Note over W,DB: Ingestion loops — one per timeframe, cadence in TIMEFRAME_PERIOD_SECONDS
    Feed->>W: fetch_candles(symbol, tf)
    W->>DB: upsert_candles()
    W->>DB: calculate_indicators() (RSI/EMA20/50/200/ATR)
    W->>SSE: POST /api/internal/rt-notify {type:"candle"}

    Note over R,ICT: Strategy scan — every 15 min (STRATEGY_PERIOD_SECONDS)
    R->>DB: SELECT enabled rows FROM "Strategy"
    loop each (symbol, timeframe)
        R->>DB: load window (OHLC + indicators, lookback bars, most-recent-first)
        R->>DB: classify regime (high/low/close)
        alt regime not in strategy.regimes
            R-->>R: candidate_gated (skip)
        else regime ok (or UNKNOWN = fail-open)
            R->>ICT: evaluate(BarWindow)
            Note right of ICT: killzone active? bias aligned?<br/>price in discount/premium?<br/>score = sweep .30 + OB .20 + FVG .20 + OTE .15<br/>require ≥2 arrays AND score≥min AND RR≥2
            ICT-->>R: 0..1 SignalCandidate {entry, sl, tp, reason, drawings[]}
        end
        R->>G: POST /api/signals/candidate (camelCase payload)
    end

    Note over G,RE: The gate — AI then RISK, before any persistence
    G->>G: idempotency (clientId already a Signal?) -> skipped
    G->>G: cooldown (recent PENDING/ACTIVE within cooldownMs?) -> skipped
    G->>DB: fetch 50 candles + 10 indicators + upcoming HIGH-impact news
    alt fewer than 10 candles
        G-->>R: {status:"skipped", reason:"insufficient_candles"}
    else enough data
        G->>G: resolveRiskConfig (SYMBOL -> STRATEGY -> GLOBAL)
        G->>AI: POST /analyze/validate-signal {signal, candles, indicators, news}
        AI-->>G: {score, approved, reasoning, concerns}
        alt score below aiMinScore (default 70)
            G-->>R: {status:"rejected", reason:"ai_score_too_low", score}
        else AI ok
            G->>RE: validateTrade({entry, sl, tp, balance, todayLoss, news, thresholds})
            Note right of RE: position size = balance*risk% / |entry-sl|<br/>checks: daily-loss, drawdown, RR≥minRR, news window
            RE->>DB: INSERT RiskLog (always, even on reject)
            alt risk not approved
                RE-->>G: {approved:false, reasons[]}
                G-->>R: {status:"rejected", reason:"risk_rejected: ..."}
            else risk approved
                RE-->>G: {approved:true, positionSize}
                G->>DB: INSERT Signal(status=PENDING) with journaled aiReasoning
                G->>SSE: rt-notify (dashboard refresh)
                G-->>R: {status:"generated", signalId, score} (HTTP 201)
                Note over G: -> continues to Execution Policy (Section 5)
            end
        end
    end
```

### What "a signal" actually contains (journaled)

The persisted `Signal.aiReasoning` stitches **strategy reason + AI verdict + risk outcome** so nothing is unexplained (`gate.ts`):

```
Strategy ict_confluence (LONG):
  Confluence 0.70 (3 arrays): liquidity sweep SSL@1.0832; OB 1.0840-1.0846; FVG 1.0838-1.0844.
  Bias LONG, killzone ny_am, long in discount. Entry 1.0843, SL 1.0808, TP 1.0943 -> RR 2.86.

AI score: 82
AI reasoning: <model assessment>
AI concerns: <...>

Risk approved. Position size 0.01234500 units.
```

---

## 4. Signal Lifecycle (state machine)

`SignalStatus` ∈ `PENDING | ACTIVE | CLOSED | CANCELLED`.

```mermaid
stateDiagram-v2
    [*] --> PENDING: AI + risk approved, Signal saved
    PENDING --> ACTIVE: openPaperTrade, AUTO or approved CONFIRM
    PENDING --> CANCELLED: approval rejected or expired
    PENDING --> PENDING: mode OFF / breaker / caps held
    ACTIVE --> CLOSED: hit TP or SL, PnL recorded plus Journal
    CANCELLED --> [*]
    CLOSED --> [*]
```

---

## 5. Execution & Delivery Flow (one-click semi-auto)

After persistence the gate calls `decideExecution(signal)` (best-effort — a failure here never rolls back the signal).

```mermaid
sequenceDiagram
    autonumber
    participant G as Gate
    participant POL as executionPolicy
    participant DB as DB
    participant PAPER as paperTrading
    participant TG as Telegram
    participant Sam as Trader
    participant SCHED as Schedulers

    G->>POL: decideExecution(signal)
    POL->>POL: isBreakerTrippedToday()? (daily loss / drawdown)
    alt breaker tripped
        POL-->>G: held_off (mode forced OFF)
    else ok
        POL->>POL: resolve mode (SYMBOL->STRATEGY->GLOBAL, default CONFIRM)
        POL->>POL: portfolio caps (max open trades / open risk% / per-currency)
        alt mode = OFF or caps hit
            POL-->>G: held_off / blocked (Signal stays PENDING)
        else mode = AUTO
            POL->>PAPER: openPaperTrade(signalId)
            PAPER->>DB: Signal->ACTIVE, Trade(OPEN) [risk-based size]
        else mode = CONFIRM
            POL->>TG: requestApproval() -> message + Approve/Reject buttons
            TG->>Sam: alert (entry/SL/TP, RR, size, AI score, reasoning)
            Sam->>TG: tap Approve
            TG->>PAPER: webhook apv:ID -> openPaperTrade()
            PAPER->>DB: Signal->ACTIVE, Trade(OPEN)
        end
    end

    Note over SCHED,DB: Background loops
    SCHED->>DB: paper */5: reconcilePendingSignals + monitorOpenTrades
    SCHED->>DB: monitor: price≥TP / ≤SL -> Trade(CLOSED) + Signal(CLOSED) + Journal
    SCHED->>TG: expiry */1: stale PENDING approvals -> EXPIRED + Signal(CANCELLED)
    SCHED->>DB: weekly Sun 00:00: journal-review via AI
```

**Telegram is the delivery + control plane:** alerts carry the one-click buttons, and commands (`/status`, `/positions`, `/pending`, `/mode auto|confirm|off`, `/kill`, `/arm`) let Sam steer execution from his phone.

---

## 6. Dashboard Data Flow (apps/web :3100 → apps/api :4000)

Near-real-time via **SSE** (`/api/stream`) which triggers SWR cache revalidation (coalesced ~800 ms), backed by conservative polling.

```mermaid
flowchart LR
    subgraph PAGES["Next.js pages"]
        H["/ (home)"]
        S["/signals"]
        T["/trades"]
        J["/journal"]
        RK["/risk"]
        B["/backtests"]
    end
    SSE["/api/stream (SSE)"] -. "revalidate all SWR keys" .-> PAGES

    H --> EP1["/api/performance · /api/positions · /api/signals · /api/news · /api/market-context"]
    S --> EP2["/api/signals?limit=50"]
    T --> EP3["/api/positions · /api/signals · /api/performance"]
    J --> EP4["/api/journal?limit=50"]
    RK --> EP5["/api/positions · /api/performance · /api/config/(execution|risk|kill|arm)"]
    B --> EP6["/api/backtests · /api/backtests/:id · /api/backtests/run(/status)"]
```

The `/backtests` page is where ICT validation surfaces: `POST /api/backtests/run` spawns `services/data/backtester.py --save-db`, results land in the `BacktestRun` table, and the page renders the per-(strategy,symbol,timeframe) metrics table + `EquityCurve` with a colour-coded verdict (POSITIVE / MARGINAL / NEGATIVE / LOW SAMPLE).

---

## 7. Cadence & timing reference

| Loop | Where | Period | Purpose |
|------|-------|--------|---------|
| Candle ingest (per TF) | `main.py` `TIMEFRAME_PERIOD_SECONDS` | 5–60 min by TF | fetch → upsert → indicators → rt-notify |
| Strategy scan | `main.py` `STRATEGY_PERIOD_SECONDS` | 15 min | run all enabled strategies → POST candidates |
| Paper tick | `scheduler.ts` `PAPER_CRON` | `*/5 * * * *` | reconcile PENDING + monitor/close open trades |
| Approval expiry | `scheduler.ts` `EXPIRY_CRON` | `* * * * *` | expire stale approvals → cancel signal |
| Weekly review | `scheduler.ts` `WEEKLY_CRON` | `0 0 * * 0` | AI behavioural review of closed trades |
| Dashboard SSE | `useRealtime.ts` | event-driven | revalidate SWR on candle/signal events |

---

## 8. The ICT engine in detail (where the signal is *born*)

How `ict_confluence` decides — the gate before the gate (`strategies/ict/confluence.py`, primitives in `strategies/ict/primitives.py`). All geometry is **look-ahead-safe** (swings confirmed `k` bars later; FVGs/OBs reference only closed candles).

```mermaid
flowchart TD
    A["Decision bar (latest closed)"] --> KZ{"Killzone active?<br/>(London / NY-AM)<br/>intraday only"}
    KZ -- no --> X0["no signal"]
    KZ -- yes --> BIAS{"EMA bias known?<br/>EMA50 vs EMA200"}
    BIAS -- "opposes direction" --> X1["skip direction"]
    BIAS -- "aligned / unknown" --> PD{"Premium/Discount<br/>(price vs equilibrium<br/>of last swing range)"}
    PD -- "long in premium / short in discount" --> X2["no signal"]
    PD -- "long in discount / short in premium" --> ARR["Score stacked PD arrays at the zone"]

    ARR --> SW["liquidity sweep (SSL/BSL) +0.30"]
    ARR --> OB["order block retest +0.20"]
    ARR --> FV["FVG tap at CE +0.20"]
    ARR --> OT["OTE 0.62-0.79 band +0.15"]

    SW & OB & FV & OT --> SCORE{"score ≥ min_score 0.40<br/>AND ≥ 2 arrays<br/>AND RR ≥ 2 ?"}
    SCORE -- no --> X3["no signal (skip = correct)"]
    SCORE -- yes --> EMIT["Emit 1 SignalCandidate<br/>entry = strongest array level<br/>SL = beyond invalidation minus buffer*ATR<br/>TP = next opposing liquidity or 2R<br/>reason + drawings list"]
    EMIT --> GATE["POST /api/signals/candidate"]
```

> **Status (2026-06-24):** the ICT detectors + aggregator are built and backtested. On EURUSD 60min the aggregator's walk-forward OOS shows PF 1.30 / +0.195R with walk-forward efficiency 1.13 — promising but only 39 OOS trades, **not yet deployable**. The engine currently surfaces on the dashboard as **backtests**; it is not enabled as a live `Strategy` row, and per `CLAUDE.md` it must beat a random baseline + reach a larger sample before going live. See `docs/research/ict-signal-engine-build-plan.md`.

---

## Appendix — key code anchors

| Concern | File |
|---|---|
| Worker orchestration | `services/data/main.py` |
| Strategy driver | `services/data/strategy_runner.py` |
| ICT primitives / aggregator | `services/data/strategies/ict/primitives.py`, `.../confluence.py` |
| Killzones | `services/data/strategies/ict/killzones.py` |
| Signal gate | `apps/api/src/signals/gate.ts` |
| Risk engine | `apps/api/src/risk/riskEngine.ts` |
| Execution policy | `apps/api/src/execution/executionPolicy.ts` |
| Paper trading | `apps/api/src/execution/paperTrading.ts` |
| Telegram approvals | `apps/api/src/telegram/approvals.ts` |
| AI validation | `services/ai/src/main.py` (`/analyze/validate-signal`) |
| Dashboard realtime | `apps/web/lib/useRealtime.ts`, `apps/web/lib/api.ts` |
| Backtests API | `apps/api/src/routes/backtests.routes.ts` |
