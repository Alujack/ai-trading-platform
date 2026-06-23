# Control Layer + Telegram Confirm-to-Trade — Design & Architecture

_Companion to `00-audit.md` and `04-strategy-support-roadmap.md`. This document specifies three things you asked for: (1) a clear **trading rulebook**, (2) a **risk/reward model** that is fully **runtime-controllable**, and (3) a **Telegram confirm-to-trade workflow** with a per-scope **AUTO ↔ CONFIRM** mode toggle and a global kill-switch. It is a design spec — no code is changed by this document. File references point at the real code so each step is concrete._

_Date: 2026-06-23 · Status: design, pending approval to build._

---

## 0. Why this exists

Today the platform is already past "basic": every strategy funnels through one AI + risk **gate** ([`apps/api/src/signals/gate.ts`](../../apps/api/src/signals/gate.ts)), the risk engine sizes positions and enforces circuit breakers ([`apps/api/src/risk/riskEngine.ts`](../../apps/api/src/risk/riskEngine.ts)), and a paper-trade loop opens/monitors/journals trades ([`apps/api/src/execution/paperTrading.ts`](../../apps/api/src/execution/paperTrading.ts)). But three things block it from being a controllable, human-supervised system:

1. **The rules are hardcoded.** `MIN_RR`, `DAILY_LOSS_LIMIT_PCT`, `MAX_DRAWDOWN_PCT`, the ±30-min news window, `riskPercent`, `maxOpen` — all live as constants or `.env` values read at process start. You cannot change them at runtime, per strategy, or per symbol, and there is no audit of who changed what.
2. **Execution is unconditionally automatic.** `sweepPendingSignals()` opens *every* `PENDING` signal on the next 5-minute cron tick ([`paperTrading.ts` `sweepPendingSignals`](../../apps/api/src/execution/paperTrading.ts), [`scheduler.ts`](../../apps/api/src/execution/scheduler.ts)). There is no human-in-the-loop step, no auto/manual switch, and no kill-switch.
3. **There is no notification/approval channel.** Telegram appears only as a future idea in `docs/trading_roadmap.md` Phase 7. Nothing sends a signal out, and nothing accepts an approve/reject decision back.

This design closes all three, and keeps the project's non-negotiable rule intact: **the risk engine is always called before any execution** (`CLAUDE.md`). The Telegram step is added *after* AI + risk approval, never instead of it.

---

## 1. Target architecture at a glance

```
 Strategy runner (Python)                 services/data/strategy_runner.py
        │  POST /api/signals/candidate
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  GATE  (apps/api/src/signals/gate.ts)                         │
 │   1. idempotency + cooldown                                   │
 │   2. AI validate-signal  (services/ai validate-signal)        │
 │   3. risk engine validateTrade()  ← reads RiskConfig (NEW)    │
 │   → persists Signal(status = PENDING)                         │
 └─────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  EXECUTION DECIDER  (NEW: executionPolicy.ts)                 │
 │   reads effective ExecutionMode for (global→strategy→symbol)  │
 │                                                              │
 │   OFF        → Signal stays PENDING, logged, no action        │
 │   AUTO       → openPaperTrade() immediately (today's behavior)│
 │   CONFIRM    → create Approval(PENDING) + send Telegram alert │
 └─────────────────────────────────────────────────────────────┘
        │                                   │
        │ AUTO                              │ CONFIRM
        ▼                                   ▼
 openPaperTrade()                    Telegram message w/ Approve/Reject
 (paperTrading.ts)                          │
                                            ▼
                              ┌──────────────────────────────────┐
                              │ Telegram webhook (NEW route)      │
                              │  /internal/telegram/webhook       │
                              │  verify secret → parse callback   │
                              │  Approve → openPaperTrade()        │
                              │  Reject  → Signal=CANCELLED        │
                              │  edit message to stamp outcome    │
                              └──────────────────────────────────┘
```

Two new pieces of state — **runtime config** (RiskConfig / ExecutionMode) and **approvals** (Approval) — and two new surfaces — an **execution decider** between the gate and execution, and a **Telegram bridge** (outbound alert + inbound webhook). Everything else stays where it is.

---

## 2. Part A — The Trading Rulebook

This part writes down the rules the system trades by. Most already exist in code; this consolidates them into one referenceable place and marks which become configurable in Part C. Nothing here is advice on *what* to trade — it is the operating contract the engine enforces.

### 2.1 Lifecycle of one trade (the rule chain)

A trade may only come into existence by passing, in order, every gate below. Each gate can only **reject or shrink** — never loosen — the one before it.

1. **Strategy trigger.** A registered, `enabled` strategy emits a `SignalCandidate` on the latest bar ([`services/data/strategies/`](../../services/data/strategies/)). Each strategy declares its own entry logic and the `regimes` it may trade in.
2. **Idempotency + cooldown.** The gate drops duplicates (`clientId`) and suppresses a new signal while one is still open for the same `(symbol, timeframe, strategy)` within `cooldownMs` ([`gate.ts`](../../apps/api/src/signals/gate.ts)).
3. **Data sufficiency.** Require ≥ 10 candles of history or skip ([`gate.ts`](../../apps/api/src/signals/gate.ts)).
4. **AI validation.** `POST /analyze/validate-signal` scores 0–100 with reasoning + concerns; must clear `aiMinScore` (default 70, per-strategy) ([`services/ai/src/prompts.py`](../../services/ai/src/prompts.py) `VALIDATE_SIGNAL_SYSTEM`).
5. **Risk validation.** `validateTrade()` enforces position sizing, RR floor, daily-loss, drawdown, and the news blackout (Part B). Writes a `RiskLog` row every time ([`riskEngine.ts`](../../apps/api/src/risk/riskEngine.ts)).
6. **Persist as PENDING.** Only AI- *and* risk-approved candidates become a `Signal` ([Prisma `Signal`](../../apps/api/prisma/schema.prisma)).
7. **Execution decision (NEW).** The execution decider applies the effective `ExecutionMode` (OFF / AUTO / CONFIRM) — Part C and D.
8. **Open trade.** `openPaperTrade()` re-sizes from the live `RiskConfig` and opens the position ([`paperTrading.ts`](../../apps/api/src/execution/paperTrading.ts)).
9. **Manage & exit.** `monitorOpenTrades()` exits on static TP/SL today; Part B notes the planned ATR-trail / time-stop extensions (Phase 8 of the strategy roadmap).
10. **Journal.** Every close writes a `Journal` row; the weekly AI review summarizes behavior ([`paperTrading.ts` `runWeeklyJournalReview`](../../apps/api/src/execution/paperTrading.ts)).

### 2.2 Entry rules (per strategy, summarized)

| Strategy | File | Direction | Core entry condition | Stop / Target | Default regime |
|---|---|---|---|---|---|
| `trend_ema` | [`trend_ema.py`](../../services/data/strategies/trend_ema.py) | LONG | EMA20 > EMA50, RSI ∈ [40,55] pullback, ATR > floor | SL 1.5×ATR / TP 3×ATR (1:2) | TRENDING |
| `meanrev_rsi` | [`meanrev_rsi.py`](../../services/data/strategies/meanrev_rsi.py) | LONG/SHORT | RSI<30 & close>EMA200 / RSI>70 & close<EMA200 | SL 1.5×ATR / TP 3×ATR (1:2) | RANGING |
| `scalp_ema` | [`scalp_ema.py`](../../services/data/strategies/scalp_ema.py) | LONG/SHORT | EMA20/50 stack + price vs EMA20 + RSI band | Fixed pips: SL 50 / TP 100 (1:2) | any |

These are the *deterministic* trigger rules. They are intentionally simple; the AI layer and risk engine are the quality filters on top. All strategy parameters are already data-driven via the `Strategy.params` JSON column ([Prisma `Strategy`](../../apps/api/prisma/schema.prisma)) — so thresholds are editable without code. Part C extends that same idea to risk and execution.

### 2.3 Exit & invalidation rules

- **Take-profit / stop-loss:** static levels carried on the `Signal`; `evaluateExit()` closes when price crosses either ([`paperTrading.ts` `evaluateExit`](../../apps/api/src/execution/paperTrading.ts)).
- **News invalidation:** a HIGH-impact event inside the configured window blocks *new* entries (Part B). Managing *open* trades through news is a Phase-8 item.
- **Planned (strategy roadmap Phase 8):** ATR trailing stop for trend/breakout, time-based exit for mean reversion, Donchian reverse-break for breakout. This control design is forward-compatible with those — they become exit-rule fields on `Strategy.params` / `RiskConfig`.

### 2.4 Standing rules (the "NEVER break" list, restated and enforced)

From `CLAUDE.md`, mapped to where they are enforced:

- Risk engine called before any execution → gate always calls `validateTrade()` ([`gate.ts`](../../apps/api/src/signals/gate.ts)); the new decider runs *after* this, never around it.
- Never hardcode API keys → Telegram token + webhook secret live in `.env` (Part D §6).
- Every signal journaled with reasoning → `Signal.aiReasoning` + `Journal`; approvals add an audit trail (Part D §5).
- Backtest before live; paper before real → unchanged; live brokerage stays out of scope and gated behind the existing roadmap.

---

## 3. Part B — Risk & Reward Model

The current engine is sound; the change is making every number a controllable value with a safe default, plus three additions the strategy roadmap already calls for. All of this reads from one **`RiskConfig`** record resolved per scope (Part C).

### 3.1 What exists today (and becomes configurable)

| Rule | Today (hardcoded) | File | Becomes |
|---|---|---|---|
| Risk per trade | `PAPER_RISK_PERCENT` env, default 1% | [`gate.ts` `readAccountState`](../../apps/api/src/signals/gate.ts) | `RiskConfig.riskPerTradePct` |
| Min risk/reward | `MIN_RR = 2` | [`riskEngine.ts`](../../apps/api/src/risk/riskEngine.ts) | `RiskConfig.minRR` |
| Daily loss limit | `DAILY_LOSS_LIMIT_PCT = 3` | [`riskEngine.ts`](../../apps/api/src/risk/riskEngine.ts) | `RiskConfig.dailyLossLimitPct` |
| Max drawdown | `MAX_DRAWDOWN_PCT = 10` | [`riskEngine.ts`](../../apps/api/src/risk/riskEngine.ts) | `RiskConfig.maxDrawdownPct` |
| News blackout | ±30 min on HIGH events | [`riskEngine.ts` `isNewsWindow`](../../apps/api/src/risk/riskEngine.ts) | `RiskConfig.newsBeforeMin` / `newsAfterMin` |
| Max open trades | `PAPER_MAX_OPEN_TRADES` env, default 5 | [`positions.routes.ts`](../../apps/api/src/routes/positions.routes.ts) | `RiskConfig.maxOpenTrades` (now *enforced* at entry) |
| AI score floor | `DEFAULT_AI_MIN_SCORE = 70` | [`gate.ts`](../../apps/api/src/signals/gate.ts) | `RiskConfig.aiMinScore` (strategy may override down) |

> Note one current inconsistency to fix while here: `maxOpen` is only *reported* by `positions.routes.ts`, never enforced before opening a trade. The new decider enforces `maxOpenTrades` as a hard pre-trade check.

### 3.2 The reward side — how RR is defined and enforced

Risk/reward is the ratio of expected reward to risked amount on a single trade:

```
risk   = |entry − stopLoss|
reward = |takeProfit − entry|
RR     = reward / risk           (validateRiskReward, riskEngine.ts)
```

A candidate is rejected when `RR < minRR` (default 2.0, i.e. risk 1 to make 2). This is enforced in two places by design — the strategies pre-shape SL/TP to 1:2, and the engine re-checks independently so a mis-parameterized strategy can never sneak a 1:1 trade through. The AI prompt also penalizes RR worse than 1:1.5 as a third, softer check ([`prompts.py`](../../services/ai/src/prompts.py)).

### 3.3 Position sizing — how much we risk

Unchanged formula, now reading the configurable percent:

```
riskAmount = accountBalance × (riskPerTradePct / 100)
lotSize    = riskAmount / |entry − stopLoss|        (calculatePositionSize, riskEngine.ts)
```

This keeps **dollar risk constant per trade** regardless of stop distance — the single most important sizing property. Defaults: 1% per trade (the roadmap's "never >1%" rule).

### 3.4 Additions (recommended defaults, all configurable)

These are new checks the strategy roadmap (Phase 8) flagged; the control layer makes them first-class config so they can be tuned or disabled safely:

1. **Max concurrent open trades** — `maxOpenTrades` (default 5), enforced at entry, not just displayed.
2. **Max total open risk** — `maxOpenRiskPct` (default 5% of equity): sum of `riskAmount` across open trades may not exceed this. Stops "five 1% trades = 5% at once" surprises.
3. **Correlation / per-currency cap** — `maxRiskPerCurrencyPct` (default 2%): long EUR/USD + long GBP/USD is a doubled-USD bet; cap exposure per base/quote currency. Start with a static currency map.
4. **Per-strategy risk budget** — each `Strategy` may carry `maxConcurrent` and a risk allocation; negative-skew strategies (mean reversion) get tighter caps.
5. **Cooldown after a loss / after circuit-break** — optional `postLossCooldownMin` to avoid revenge entries (the journal-review prompt already flags this behavior).

### 3.5 Circuit breakers (the safety floor)

Two breakers already exist and stay; both feed the kill-switch in Part C:

- **Daily loss breaker:** when today's realized loss > `dailyLossLimitPct` of balance, `validateTrade()` rejects and writes `RiskLog.circuitBreakerTripped = true`. Design addition: when tripped, the execution decider flips effective mode to **OFF for the rest of the UTC day** and sends one Telegram notice.
- **Max-drawdown breaker:** when drawdown from peak > `maxDrawdownPct`, reject and require manual re-enable (mode stays OFF until a human re-arms it). This matches the roadmap's "system stops until reviewed."

`computeTodayLoss()` already aggregates today's realized losses ([`gate.ts`](../../apps/api/src/signals/gate.ts)); the decider reuses it.

---

## 4. Part C — Everything Controllable (auto *and* manual)

The core of your request: every rule above, plus the execution behavior, must be changeable at runtime, per scope, with validation and an audit trail — and the **auto behavior itself must be controllable** (you can switch a strategy or symbol between fully automatic and require-confirmation, or turn it off entirely).

### 4.1 Three scopes, with precedence

Config resolves **most-specific-wins**:

```
symbol-level  ►  strategy-level  ►  global default
```

Example: global `CONFIRM`, but `scalp_ema` set to `AUTO`, but `XAUUSD` set to `OFF` → a `scalp_ema` signal on `XAUUSD` resolves to `OFF`; the same strategy on `EURUSD` resolves to `AUTO`; a `trend_ema` signal on `EURUSD` resolves to global `CONFIRM`.

The same precedence applies to every `RiskConfig` field, so you can, e.g., run 0.5% risk globally but 0.25% on BTCUSD.

### 4.2 Execution mode — the AUTO ↔ CONFIRM toggle

A single enum drives whether the bot acts on its own:

```prisma
enum ExecutionMode {
  OFF       // kill-switch: signals are generated + logged, never executed
  AUTO      // open immediately after AI+risk approval (today's behavior)
  CONFIRM   // require a Telegram Approve before opening
}
```

- **Global kill-switch** = set global mode `OFF`. One toggle halts all execution while keeping signal generation/observability alive.
- **Per-strategy / per-symbol overrides** let you trust a proven strategy (`AUTO`) while keeping a new one on a leash (`CONFIRM`).
- **Auto-degrade:** a tripped daily-loss breaker forces effective `OFF` until reset; max-drawdown requires manual re-arm. This is *controllable* too — the thresholds that trigger it are in `RiskConfig`.

### 4.3 Data model (new tables)

```prisma
model RiskConfig {
  id                    String   @id @default(cuid())
  scope                 String   // "GLOBAL" | "STRATEGY" | "SYMBOL"
  scopeKey              String   // ""(global) | strategyName | symbol
  riskPerTradePct       Decimal  @db.Decimal(6,4)  // default 1.0
  minRR                 Decimal  @db.Decimal(6,4)  // default 2.0
  dailyLossLimitPct     Decimal  @db.Decimal(6,4)  // default 3.0
  maxDrawdownPct        Decimal  @db.Decimal(6,4)  // default 10.0
  maxOpenTrades         Int                        // default 5
  maxOpenRiskPct        Decimal  @db.Decimal(6,4)  // default 5.0
  maxRiskPerCurrencyPct Decimal  @db.Decimal(6,4)  // default 2.0
  newsBeforeMin         Int                        // default 30
  newsAfterMin          Int                        // default 30
  aiMinScore            Int                        // default 70
  enabled               Boolean  @default(true)
  updatedAt             DateTime @updatedAt
  @@unique([scope, scopeKey])
}

model ExecutionSetting {
  id        String        @id @default(cuid())
  scope     String        // "GLOBAL" | "STRATEGY" | "SYMBOL"
  scopeKey  String
  mode      ExecutionMode @default(CONFIRM)
  updatedAt DateTime      @updatedAt
  @@unique([scope, scopeKey])
}

model ConfigAudit {
  id        String   @id @default(cuid())
  actor     String   // "ui:yan" | "telegram:<id>" | "system"
  entity    String   // "RiskConfig" | "ExecutionSetting"
  scope     String
  scopeKey  String
  before    Json
  after     Json
  createdAt DateTime @default(now())
  @@index([createdAt])
}
```

A tiny **resolver** (`apps/api/src/config/resolve.ts`, NEW) reads the three scope rows, layers them, caches the result in Redis (invalidated on write), and hands a single resolved `EffectiveConfig` to the gate, the risk engine, and the decider. The risk engine's current constant defaults become the seed values of the GLOBAL row, so behavior is identical on day one until something is changed.

### 4.4 API surface (new)

```
GET    /api/config/risk?scope=GLOBAL|STRATEGY|SYMBOL&scopeKey=...   → resolved + raw rows
PUT    /api/config/risk          { scope, scopeKey, ...fields }      → validate, write, audit
GET    /api/config/execution                                        → all mode rows + effective map
PUT    /api/config/execution     { scope, scopeKey, mode }          → write, audit, Redis bust
POST   /api/config/kill          { reason }                         → sets GLOBAL mode = OFF (panic)
POST   /api/config/arm           { reason }                         → clears OFF after a breaker
```

All writes go through Zod validation ([existing `middleware/validate.ts`](../../apps/api/src/middleware/validate.ts) pattern) with hard bounds (e.g. `riskPerTradePct` ∈ (0, 5], `minRR` ≥ 1) so the UI can't set a self-destructive value, and every write appends a `ConfigAudit` row.

### 4.5 UI surface

The dashboard already has a `RiskEnginePanel` and an `AiSettingsModal` ([`apps/web/app/components/dash/RiskEnginePanel.tsx`](../../apps/web/app/components/dash/RiskEnginePanel.tsx), [`AiSettingsModal.tsx`](../../apps/web/app/components/AiSettingsModal.tsx)) — the same pattern extends to a **Controls** panel: sliders/inputs for each `RiskConfig` field, a three-way mode switch (OFF/AUTO/CONFIRM) at global + per-strategy + per-symbol, a prominent red **KILL** button, and a live "effective settings for X" preview so the precedence is never a mystery. Mode changes are also possible from Telegram (Part D §4) so you can flip to AUTO or OFF from your phone.

---

## 5. Part D — Telegram Confirm-to-Trade Workflow

When the effective mode is `CONFIRM`, a new `PENDING` signal does not open a trade. Instead it creates an **Approval** and sends a Telegram message containing the full trade plan and *why the AI wants it*, with **Approve / Reject** buttons. The trade opens only on Approve.

### 5.1 Signal & approval lifecycle

```
Signal.status:  PENDING ──CONFIRM──▶ (Approval created) ──Approve──▶ ACTIVE (trade opened)
                   │                          │
                   │                          └────Reject / Expire──▶ CANCELLED
                   └────AUTO──▶ ACTIVE
```

```prisma
enum ApprovalStatus { PENDING APPROVED REJECTED EXPIRED }

model Approval {
  id            String         @id @default(cuid())
  signalId      String         @unique
  signal        Signal         @relation(fields: [signalId], references: [id])
  status        ApprovalStatus @default(PENDING)
  chatId        String         // Telegram chat the alert went to
  messageId     String?        // Telegram message id (for editMessageText)
  decidedBy     String?        // Telegram user id/name who acted
  decidedAt     DateTime?
  expiresAt     DateTime       // signal is time-sensitive; auto-expire
  createdAt     DateTime       @default(now())
  @@index([status])
  @@index([expiresAt])
}
```

`Signal` gains `@relation` to `Approval`, and `SignalStatus` already has `CANCELLED` so no enum change is needed there.

### 5.2 The decider (new module)

`apps/api/src/execution/executionPolicy.ts` (NEW) is called by the gate right after a `Signal` is persisted (or by the existing `sweepPendingSignals` loop, see §5.7):

```
decide(signal):
  mode = resolveExecutionMode(signal.strategyName, signal.symbol)   # precedence
  if breakerTrippedToday(): mode = OFF
  switch mode:
    OFF:     log + leave PENDING (or CANCELLED if you prefer hard-stop); no trade
    AUTO:    openPaperTrade(signal.id)            # unchanged path
    CONFIRM: createApproval(signal) + sendTelegramAlert(signal)
```

This is the *only* new branch in the execution path; `openPaperTrade()` and the risk engine are untouched and still authoritative.

### 5.3 The alert message (all reasoning + full position detail)

Sent via `POST https://api.telegram.org/bot<token>/sendMessage` with an inline keyboard. It carries everything you asked for — the position spec, the numbers, and the AI's case:

```
🟢 SIGNAL — XAUUSD 60min · LONG · trend_ema
Mode: CONFIRM · expires in 15m

PLAN
• Entry      2348.50
• Stop       2333.10   (−15.40,  1.5×ATR)
• Target     2379.30   (+30.80,  3.0×ATR)
• R:R        1:2.00
• Size       0.0325 lots   (risk $100.00 = 1.0% of $10,000)

WHY (AI score 78/100)
Price holding above EMA20/50 stack with RSI pulled back to 47 —
trend-continuation entry. Stop sits below the prior swing and >1×ATR,
unlikely to be wicked. No HIGH-impact news in the next 5h.

CONCERNS
• ATR elevated vs 20-bar avg — wider stop than usual
• Approaching 2350 round-number resistance

RISK CHECKS ✓  daily-loss 0.4%/3% · open risk 1.0%/5% · DD 1.2%/10%

[ ✅ Approve ]   [ ❌ Reject ]
```

Content sources, all already produced upstream: strategy + levels from the `Signal`; size/risk from `calculatePositionSize`; AI score/reasoning/concerns from `validate-signal` (already folded into `Signal.aiReasoning` by [`gate.ts`](../../apps/api/src/signals/gate.ts)); risk-check headroom from the resolved `RiskConfig` + `computeTodayLoss()`. The bot **stores no new analysis** — it just formats what the gate already computed.

The keyboard uses `callback_data` ≤ 64 bytes, e.g. `apv:<approvalId>` and `rej:<approvalId>`.

### 5.4 Inbound webhook (the decision comes back)

New route `POST /api/internal/telegram/webhook` (sibling of the existing [`newsAlert.routes.ts`](../../apps/api/src/routes/newsAlert.routes.ts)):

1. **Verify** the `X-Telegram-Bot-Api-Secret-Token` header equals `TELEGRAM_WEBHOOK_SECRET` (set when registering the webhook). Reject otherwise. This is the primary auth.
2. **Authorize** the `from.id` against `TELEGRAM_ALLOWED_USER_IDS` (allowlist) — only you can approve.
3. Parse `callback_query.data` → `("apv"|"rej", approvalId)`.
4. Load the `Approval`; if not `PENDING` or past `expiresAt`, `answerCallbackQuery` with "already decided/expired" and stop (idempotent — double-taps are safe).
5. **Approve** → `openPaperTrade(signalId)` (re-sizes from live config, the authoritative risk check happens here too); set `Approval = APPROVED`. **Reject** → `Signal = CANCELLED`, `Approval = REJECTED`.
6. `answerCallbackQuery` to clear the user's spinner, then `editMessageText` to stamp the outcome and **remove the buttons** so it can't be pressed twice:
   `✅ Approved by Yoeurn · trade opened (id …) 2026-06-23 14:02 UTC`.
7. Respond `200` quickly; do Telegram edits fire-and-forget so a slow edit never blocks.

Also handle the simple command set in §5.6 in the same webhook (messages, not just callbacks).

### 5.5 Expiry & timeouts (signals are perishable)

A new lightweight sweep (extend [`scheduler.ts`](../../apps/api/src/execution/scheduler.ts)) runs each minute: any `Approval` past `expiresAt` and still `PENDING` → set `EXPIRED`, `Signal = CANCELLED`, and `editMessageText` to "⌛ Expired — not taken." Default TTL 15 min, configurable (`approvalTtlMin` on `RiskConfig`). This guarantees a stale entry never opens at a bad price hours later.

### 5.6 Bot commands (control from your phone)

The webhook also accepts text commands from allowlisted users, so the kill-switch and mode toggle are reachable without the dashboard:

```
/mode auto|confirm|off [strategy|symbol]   set execution mode for a scope
/kill                                      global OFF (panic)
/arm                                       clear OFF after a breaker
/status                                    equity, open trades, day P&L, mode, breaker state
/positions                                 list open trades + unrealized P&L
/pending                                   list approvals awaiting a decision
```

Each command writes a `ConfigAudit` row with `actor = "telegram:<id>"`.

### 5.7 Where the decider hooks in (two options)

- **Option 1 — inline at the gate (recommended).** After `prisma.signal.create(... PENDING ...)` in [`gate.ts`](../../apps/api/src/signals/gate.ts), call `decide(signal)`. Lowest latency; the alert fires the instant a signal is born.
- **Option 2 — in the sweep loop.** Move the `decide()` call into `sweepPendingSignals()` ([`paperTrading.ts`](../../apps/api/src/execution/paperTrading.ts)), so the existing 5-min cron drives it. Simpler, but adds up to 5 min of delay — bad for scalps.

Recommendation: Option 1 for the alert, and keep a reconciliation pass in the sweep that picks up any `PENDING` signal with no `Approval` and no trade (covers a missed webhook or a restart). The sweep must be taught to **skip signals that are awaiting approval** so it never races the human (today it would open them).

### 5.8 Transport choice — webhook vs polling

Webhook (with secret token) is the design default: lower latency, no idle polling, and it fits the existing Express app. For local dev without a public URL, a long-poll fallback (`getUpdates`) or an n8n Telegram node (you already run n8n, see [`infra/n8n/`](../../infra/n8n/)) can stand in. n8n is in fact a clean place to host the whole Telegram bridge if you'd rather keep it out of the API — both are noted; the API-native webhook is recommended for tighter coupling to the execution path.

---

## 6. Security & failure modes

- **Secrets in `.env` only:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ALLOWED_USER_IDS`, `TELEGRAM_CHAT_ID`. Add to `.env.example` (placeholders, never real values) per the `CLAUDE.md` rule.
- **Webhook auth = secret-token header + user allowlist.** Both must pass. Unknown chat/user → ignored and logged.
- **Idempotency everywhere:** approval state machine rejects double decisions; callback re-taps are no-ops; `editMessageText` removes buttons after a decision.
- **Fail-safe defaults:** if Telegram is unreachable when sending an alert, the signal stays `PENDING` (never auto-opens as a fallback) and is retried by the sweep; if the AI service is down the gate already returns `skipped` (no signal) — so an outage fails *closed*, never into an unsupervised trade.
- **Breaker > mode:** a tripped daily-loss/drawdown breaker forces OFF regardless of a strategy's AUTO setting.
- **Audit:** `ConfigAudit` + `Approval.decidedBy/decidedAt` give a full who-changed-what and who-approved-what record.

---

## 7. Phased build plan

Continues the numbering from `04-strategy-support-roadmap.md` (which ends at Phase 9). Each phase is independently shippable and leaves the system working.

### Phase 10 — Runtime config foundation (~2 days)
**Why:** nothing else is controllable until the rules are data. **Steps:** add `RiskConfig`, `ExecutionSetting`, `ConfigAudit` models + migration; seed GLOBAL from current constants; build the `resolve.ts` resolver with Redis caching; refactor `riskEngine.ts`, `gate.ts`, `positions.routes.ts` to read resolved config instead of constants/env; add the `/api/config/*` routes with Zod bounds + audit. **Done when:** changing `minRR` via `PUT /api/config/risk` immediately changes which signals pass, with a `ConfigAudit` row written and no redeploy.

### Phase 11 — Execution decider + modes (~1.5 days)
**Why:** make AUTO controllable and add OFF. **Steps:** `executionPolicy.ts` with `decide()`; wire it into the gate (Option 1) and teach `sweepPendingSignals` to skip awaiting-approval signals + reconcile; implement `/api/config/kill` + `/arm`; enforce `maxOpenTrades` / `maxOpenRiskPct` / `maxRiskPerCurrencyPct` and the breaker→OFF auto-degrade. **Done when:** flipping a strategy AUTO→OFF→AUTO changes execution live; KILL halts all opens while signals still log.

### Phase 12 — Telegram bridge (~2–3 days)
**Why:** the human-in-the-loop you asked for. **Steps:** `Approval` model + migration; outbound `telegram.ts` (sendMessage + inline keyboard, message formatter from §5.3); inbound `/api/internal/telegram/webhook` (secret + allowlist + callback parse + edit-message); expiry sweep in `scheduler.ts`; `.env.example` additions; webhook registration script. **Done when:** a CONFIRM-mode signal sends a fully-detailed alert, Approve opens the trade and stamps the message, Reject cancels it, and a stale one auto-expires.

### Phase 13 — Controls UI + bot commands (~2 days)
**Why:** drive it all from dashboard or phone. **Steps:** Controls panel (risk fields + 3-way mode switches + KILL + effective-config preview); `/mode`, `/kill`, `/arm`, `/status`, `/positions`, `/pending` command handlers. **Done when:** every config and mode is editable from the UI and from Telegram, both audited.

### Phase 14 (optional) — Hardening (~1–2 days)
Rate-limit the webhook; alert on breaker trips and on AI/Telegram outages; per-strategy risk budgets; metrics for approval latency and approve/reject ratio fed into the weekly journal review.

---

## 8. Open decisions for you

1. **OFF semantics:** when a scope is OFF, should a new signal stay `PENDING` (resumable if you flip to AUTO/CONFIRM within its TTL) or be `CANCELLED` immediately? Default proposed: stay `PENDING`, expire on TTL.
2. **Approval TTL:** 15 min default — shorter for scalps (`scalp_ema` is intraday), longer for daily setups? Could be per-strategy.
3. **Multiple approvers / channels:** single chat + single user now, or an allowlist of several with one-approval-wins?
4. **Telegram host:** API-native webhook (recommended) vs the existing n8n instance.
5. **Live trading:** this design stays paper-only, consistent with `CLAUDE.md`. Confirm that the Telegram approval should *also* be the gate for real-broker execution later (it should), so the contract is built right once.

---

## 9. Summary

The system already has the hard parts — a unified gate, a real risk engine, AI validation, and paper execution. This design adds the **control plane** (data-driven risk + execution config with precedence, validation, and audit), an **execution decider** that makes "automatic" a controllable choice (OFF / AUTO / CONFIRM) with a global kill-switch, and a **Telegram confirm-to-trade bridge** that puts a human approval — with the full plan and the AI's reasoning — in front of any trade you choose to supervise. Nothing here weakens the standing rule that the risk engine runs before every execution; the approval step sits *after* it, as the last line of defense before a position opens.
