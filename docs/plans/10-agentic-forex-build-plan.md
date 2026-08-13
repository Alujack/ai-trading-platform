# Bank the Green Day — Agentic Forex Build Plan

> ai-trading-platform · build plan · 2026-08-12
> Edge: `ict_sweep_mss` / XAUUSD
> Source: converted from the published Claude artifact ("Bank the Green Day", 2026-08-12)

The agentic forex system you sketched already exists in this repo — data workers, strategy agents, an LLM validator, a deterministic risk engine, Telegram-supervised execution, an MT5 bridge, journal and review loops. What's missing isn't architecture. It's clean data, a re-validated edge, and the discipline to treat "2% a day" as a ceiling you bank, not a return you promise.

---

## The 2% question, answered first

**"Make 2% every day" is not an engineering target.** Compounded over a trading year, 1.02^252 ≈ 141× starting capital — anyone claiming that reliably is selling something. The honest expectation from this platform's one validated edge is roughly **2–4% per month** at 1% risk per trade, with losing weeks along the way.

**But "2% a day" as a risk frame is already implemented.** The platform's default config expresses exactly the sane version of the idea — and it's live in code, not aspiration:

| Rule | Config | Default | Effect |
|---|---|---|---|
| Risk one to make two | `riskPerTradePct` + `minRR` | 1% / 2R | a full winner ≈ **+2% day** |
| One trade per day | `maxTradesPerDay` | 1 | no revenge trading |
| Bank the green day | `dailyProfitTargetPct` | 2% | breaker holds all new trades after +2% realized, until next UTC day |
| Cap the red day | `dailyLossLimitPct` | 2–3% | breaker halts the day's trading |
| Floor the account | `maxDrawdownPct` | 10% | hard stop on the strategy |

Defaults in `apps/api/src/config/defaults.ts`; enforced by `riskEngine.ts` and the execution decider's circuit breaker (`executionPolicy.ts` — "bank the green day" is a comment in the code). All tunable per strategy/symbol from the dashboard, inside hard bounds the API refuses to exceed.

So the goal of this plan is not to build a 2%/day machine. It is to get one statistically real edge flowing through the already-built pipeline, capped at 2% a day in both directions, and prove expectancy on paper before a dollar goes live.

---

## What already exists

Every box from the target agent architecture maps to running code. This is the actual signal path:

| Stage | Implementation | Status |
|---|---|---|
| Market data | TwelveData + MT5/Exness bridge ingestion, `services/data/fetcher.py`, `backfill_mt5.py` | **BLOCKED · stale DB** |
| Feature engine | RSI / EMA / ATR per bar, `indicator_calculator.py`; sessions + regime, `sessions.py`, `regime.py` | BUILT |
| Strategy agents | ICT detector family (sweep+MSS, order block, FVG, killzones, confluence) + registry, `services/data/strategies/` | BUILT |
| LLM validator | FastAPI service, multi-provider, structured verdicts: score + approved + concerns, `services/ai/src/` | BUILT |
| Risk engine | Sizing, daily loss, drawdown, RR, news window, session budgets — deterministic, LLM cannot override, `apps/api/src/risk/riskEngine.ts` | BUILT |
| Execution decider | OFF / CONFIRM / AUTO modes, Telegram approvals, portfolio caps, circuit breakers, `execution/executionPolicy.ts` | BUILT |
| Brokers | Paper broker + live Exness via Windows MT5 bridge, `execution/broker/`, `services/mt5bridge/app.py` | BUILT |
| Position manager | 5-min reconcile loop, live monitor, trailing stops, 15-s scalp manager, `execution/scheduler.ts` | BUILT |
| Journal + review | Every signal journaled with AI reasoning; daily briefing, weekly AI journal review, `dailyBriefing.ts`, `paperTrading.ts` | BUILT |

The one gate everything funnels through is `gateCandidate()` in `apps/api/src/signals/gate.ts`: LLM score ≥ 70 *and* explicit approval *and* risk-engine pass, or no signal is stored. The LLM reasons; deterministic code sizes, validates, and executes. That's the same separation of powers the agent sketch called for.

---

## The evidence: one edge is real, the rest are dead

| Strategy | Timeframe | OOS expectancy | PF | vs. random | Verdict |
|---|---|---|---|---|---|
| `ict_sweep_mss` | XAUUSD 60min | +0.23R | 1.48 | p = 0.000 | **TRADE IT** |
| `ict_sweep_mss` | XAUUSD 15min | +0.25R | 1.52 | p = 0.050 | **TRADE IT** (borderline) |
| `ict_sweep_mss` | XAUUSD 1min | +0.15R | 1.21 | beat 100% | PAPER ONLY — regime-lumpy, decays over longer windows |
| `ict_sweep_mss` | XAUUSD 5min | ≈ 0 | — | — | DEAD |
| `ict_confluence` | XAUUSD 15/60min | −0.03…−0.07R | < 1 | — | DEAD on gold |
| `ml_xau` (LightGBM) | XAUUSD 1min | −0.23R | 0.72 | below majority baseline | DEAD |
| `ml_xau` (LightGBM) | XAUUSD 5min | −0.23…−0.83R | 0.70…0.19 | −5.8% edge | DEAD |

Walk-forward + Monte-Carlo baseline results from the 2026-07-28 validation runs (see `prisma/seedIctSweepMss.ts` header); ml_xau triple-barrier metrics from `services/data/training/models/XAUUSD_*_metrics.json`. In a one-year $100 replay, 60min was positive 10 of 12 months with 3% max drawdown.

This is the platform working as designed: the validation gates exist precisely so that only `ict_sweep_mss` gets a seat at the table. Nothing in this plan adds a strategy until Phase 1 re-confirms this one on fresh broker data.

---

## What actually blocks trading today

> **The database, not the code.** The Postgres container isn't running, and its XAUUSD rows are still exchange-time (UTC+10) — the fetcher was fixed to pin UTC (and the MT5 bridge is clean), but the −10h repair migration was never applied to this instance. On top of that: ~30% weekend filler bars from TwelveData, zero volume everywhere, and no new candles since **July 5**. Session/killzone logic reads wrong against this data, which silently poisons every ICT detector.
>
> Secondary blockers: live execution needs the Windows MT5 bridge box online, and the AI validator needs a working provider key (check `services/ai` provider state — a blank key means every candidate is skipped, not traded blind).

---

## The plan

### Phase 0 — Rebuild the data foundation *(1–2 days · blocking)*

- Bring the stack up (`docker-compose up -d`); confirm Timescale + Redis healthy.
- **Wipe and re-backfill XAUUSD from the MT5 bridge**, not TwelveData — same feed you execute on, no quota, no weekend quote bars: `backfill_mt5.py` (15min/60min → 2022+, 5min → 2025-06+, 1min → 2026-05+). This sidesteps the UTC+10 repair entirely by replacing the corrupted rows.
- Recompute indicators (`indicator_calculator.py --full`); restart ingestion loops.
- *New code (small):* a data-freshness guard — strategy runner refuses to evaluate if the newest bar is older than 2× the timeframe, and a daily staleness alert lands in Telegram. Stale-data silence caused this outage to go unnoticed for a month.

**Exit gate:** Newest candle within one bar of now for every enabled timeframe; zero weekend bars; indicator coverage 100%; UTC spot-checked against the bridge.

### Phase 1 — Re-validate the edge on broker data *(2–3 days)*

- Re-run walk-forward for `ict_sweep_mss` XAUUSD 15min + 60min on the fresh Exness bars, including Aug 2026 (`walkforward.py`, costs on, regime gate on).
- Re-run the geometry-matched Monte-Carlo random baseline (`baseline_mc.py`, 100 seeds).
- Archive the report; journal the decision either way.

**Exit gate:** OOS expectancy > 0, PF ≥ 1.3, beats ≥ 95% of random baselines on 60min. **If it fails on broker data, stop here** — no agent layer fixes a dead edge.

### Phase 2 — Supervised paper trading *(4+ weeks calendar, low effort)*

- Seed the strategy (`npx tsx prisma/seedIctSweepMss.ts` — CONFIRM mode, 1% risk, 2% daily loss cap, 2% profit target; run `prisma generate` first).
- Week 1 in **CONFIRM**: every signal arrives as a Telegram approval — sanity-check entries against the chart before approving.
- Then flip to **AUTO on the paper broker only**. Let the 5-min reconcile loop and monitors run it hands-off.
- Enable the weekly AI journal review (`ENABLE_WEEKLY_REVIEW=true`) and daily briefing so the review loop runs from day one.

**Exit gate:** ≥ 30 paper trades; PF ≥ 1.2; drawdown inside the backtest envelope; live-vs-backtest expectancy gap explainable by spread/slippage. Track it weekly on the dashboard.

### Phase 3 — Agentic hardening *(1–2 weeks, parallel with paper)*

- **Regime agent → gate:** `regime.py` already computes trend/vol regime with a gating flag — verify it's on in the runner and logged with each skip.
- **News agent → data:** the risk engine's news-window check runs on an empty `NewsEvent` table. Turn on the n8n economic-calendar ingestion (plan 05) so the 30-min blackout actually fires around red-folder events.
- **Review agent → proposals, not actions:** extend the weekly review to emit *proposed config diffs* (e.g. "drop 15min, keep 60min") delivered via Telegram for one-tap human approval. The LLM never edits production config directly.
- **Market-context agent:** wire the existing `/analyze/market-context` endpoint into the daily briefing so each morning states the regime, the levels, and whether the desk should even be open.

**Exit gate:** Every skipped/blocked candidate has a logged reason; every agent recommendation is journaled and human-approved before taking effect.

### Phase 4 — Forex expansion — earn each pair *(~1 week per pair)*

- EURUSD first: sweep_mss looked promising there but undersized (39 OOS trades). Deep-backfill via the bridge, then the full Phase 1 gauntlet: walk-forward + random baseline + cost stress.
- GBPUSD / USDJPY only after EURUSD earns its slot; each needs ingestion config (`SYMBOL_MAP`, `STRATEGY_SYMBOLS`) plus the same gates.
- Correlation control already exists (`maxRiskPerCurrencyPct`, per-currency caps) — every new pair trades inside the same 2%/day account budget, not its own.

**Exit gate:** A pair is enabled only with OOS PF ≥ 1.3 and a beaten random baseline on its own data. No exceptions for "it works on gold."

### Phase 5 — The live ladder *(ongoing)*

- Exness demo through the MT5 bridge (mode CONFIRM) — proves order routing, fills, slippage, position sync.
- Micro live: $100–500, CONFIRM mode, 1% risk, minimum lots. Four consecutive weeks inside the paper envelope before AUTO.
- AUTO live with all breakers armed; scale risk only after 3 months of live PF ≥ 1.2, and never past the configured bounds.

**Exit gate:** Each rung requires the previous rung's full period green. Any breaker trip = drop one rung, review, re-earn it.

---

## What we deliberately do not do

- **Don't enable `ml_xau`.** Negative expectancy at every threshold on both trained timeframes. It stays registered but disabled until a retrained model beats its baselines.
- **Don't trade 5min, don't AUTO the 1min.** 5min failed outright; 1min is a paper-only experiment at 0.5% risk after the validated timeframes are running.
- **Don't let the LLM touch money.** It scores and approves reasoning; sizing, limits, and execution stay deterministic. The gate already enforces this — keep it that way as agents are added.
- **Don't chase the 2%.** On days the setup isn't there, the correct output of the whole system is *no trade*. The profit-target breaker exists to end good days early, not to be raised.

---

## Implementation log

**2026-08-13 — Phases 0–2 executed.**

- **Phase 0 done.** Stack restarted; corrupted XAUUSD rows (mixed UTC+10
  timestamps, ~30% weekend filler, zero volume — verified before deletion)
  wiped and re-backfilled. The MT5-bridge route was blocked — no MT5
  credentials in `.env` or the `BrokerCredential` table — so the repair used
  the UTC-pinned TwelveData path with a new weekend-bar filter in
  `services/data/fetcher.py` (Fri 22:00 → Sun 22:00 UTC dropped, BTCUSD
  exempt). New depth: 60min → 2021-02, 15min → 2024-02, 1min → 2026-06
  (5min/daily top-up pending provider quota). Exit gates passed: zero weekend
  bars, zero exchange-time leads, series fresh to the hour. Freshness guard
  shipped both sides: `strategy_runner.py` refuses series older than 2× the
  timeframe, and `apps/api/src/execution/dataFreshness.ts` sends a daily
  Telegram staleness alert from the 06:00 UTC scheduler tick.
- **Phase 1 done — see
  [docs/research/xauusd-revalidation-2026-08-13.md](../research/xauusd-revalidation-2026-08-13.md).**
  15min is dead on full history (−0.09R, PF 0.85; the July pass was a
  one-regime artifact) and is dropped. 60min: +0.26R WF-OOS / PF 1.38 fixed-params
  since Jun 2024 (reproduces July), +0.07R / PF 1.13 over the full 5.5 years,
  beats 99% of random baselines (p=0.010). Verdict: enable 60min only.
- **Phase 2 started.** `seedIctSweepMss.ts` rescoped to 60min-only and run:
  CONFIRM mode, paper broker, 1%/trade, 2% daily-loss cap, RR ≥ 2, max 2 open.
  Prisma client regenerated. Paper loop, weekly review, and daily briefing
  schedulers active.
- **Phase 2 live (2026-08-13, later same day).** Per operator instruction
  ("trade on paper first" + Telegram not yet configured), `ict_sweep_mss` was
  flipped to **AUTO on the paper broker** (`ICT_SEED_MODE=AUTO`, BROKER=paper
  verified; STRATEGY scope beats GLOBAL OFF in `resolve.ts`). Reconcile loop
  verified clean. The AI validator was upgraded per operator instruction
  ("we will use powerful llm"): Claude (`claude-opus-5`) is now the preferred
  provider in `services/ai` (model default + auto-preference order +
  16K-token/120s headroom for its always-on thinking); Gemini verified working
  and remains active until an `ANTHROPIC_API_KEY` is provided.
- **Phase 3 done (2026-08-13, same day).** Agentic hardening shipped:
  - *Regime gate:* verified ON in the worker (`STRATEGY_REGIME_GATING`
    defaults true, no override set) with every gated candidate logged
    (`candidate_gated … regime= allowed= reason=`). Note: `sweep_mss` declares
    all three known regimes — exactly the configuration that was validated —
    so the gate's live job is UNKNOWN-fail-open plus logging, not filtering.
  - *News agent → data:* both n8n workflows imported headlessly (CLI
    credential with pinned id `tradingdbcred0001`, `import:workflow`,
    activate, restart) and NewsEvent seeded immediately with a one-off
    ForexFactory fetch that mirrors workflow A's ids/upsert (73 events, dedupe
    verified). Workflow B's first scheduled run failed on n8n's default
    `$env` lockdown → AI-summary node URL patched to the in-network literal
    `http://ai:8000/analyze/news-summary` and re-imported;
    `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` added to compose (container recreated
    same day — env access live). Workflow B verified green on its 05:00 UTC
    scheduled tick: Fed RSS → Gemini summary → NewsEvent upsert.
    **Three news-layer bugs found and fixed:**
    1. The SYMBOL XAUUSD risk row had `newsBeforeMin=0`, silently disarming
       the pre-news blackout on the one traded symbol — re-armed to 30 via
       audited write (`system:plan10-phase3`), effective window now −30/+30m.
    2. The gate fetched only FUTURE events, so `isNewsWindow`'s after-release
       branch (`newsAfterMin`) was dead code — `gate.ts` now fetches a 4h
       look-back too, arming the block for 30m after each red-folder print.
    3. Workflow B stamps digests `scheduledAt=now`; with fix 2 live, a
       HIGH-rated digest every 30 min would have blocked USD trading
       permanently — digest impact is now clamped to MEDIUM in the workflow
       (commentary is context for the AI, never a blackout trigger); the two
       existing HIGH digest rows were downgraded.
  - *Review agent → proposals, not actions:* new `AgentRecommendation` table +
    `apps/api/src/execution/reviewAgent.ts`. The weekly journal review now
    sends the AI a whitelist of tunables (bounds tighter than human
    `RISK_BOUNDS`; execution mode proposals limited to OFF/CONFIRM — the agent
    can never propose AUTO; strategy scope arrays de-scope only) and journals
    every returned proposal as a PENDING row with a Telegram approve/reject
    card (`rca:`/`rcr:` callbacks). Approval re-validates against live config,
    then applies through the same audited store path as the UI
    (`writeRiskConfig`/`writeExecutionMode`, actor `"telegram:<id> via
    weekly_review"`). 72h TTL, minute-cron expiry. Smoke-tested end-to-end:
    out-of-bounds and AUTO-escalation proposals rejected at the gate; a valid
    proposal journaled → approved → applied → audited → reverted
    (`apps/api/scripts/phase3-smoke.ts`).
  - *Market-context agent:* builder extracted to
    `apps/api/src/services/marketContext.ts` (route now delegates); the 06:00
    UTC daily briefing embeds a per-traded-pair AI read (bias/levels/risks)
    and the morning Telegram brief gained a **DESK CALL** section stating the
    regime, the levels, and the risks — verified rendering with live Gemini.
  - *AI schema:* `/analyze/journal-review` accepts `tunables` + `stats` and
    returns structured `proposals`; Gemini schema converter now inlines
    Pydantic `$defs` (first nested response model). Live Gemini test produced
    in-bounds, evidence-cited proposals.
  - *Exit gate:* stale series, regime gating, risk blocks, and gate rejections
    all log reasons; every agent recommendation is a journaled DB row applied
    only after human approval. ✅
- **Phase 4 concluded (2026-08-13, same day) — EURUSD REJECTED. See
  [docs/research/eurusd-validation-2026-08-13.md](../research/eurusd-validation-2026-08-13.md).**
  Deep TwelveData backfill (40,303 bars, 2020-03 →) + full-series indicators +
  130-fold walk-forward: 205 OOS trades, **−0.207R / PF 0.69**, negative in
  every era — including −0.343R in the mid-2024+ regime that carries gold.
  July's +R read (39 trades) was small-sample noise. Random-baseline and
  cost-stress steps moot (nothing positive to qualify). Per the exit gate,
  GBPUSD/USDJPY are not attempted; **the desk stays XAUUSD 60min only.**
  Also completed: XAUUSD 5min (42,716 bars → 2026-01) and daily (4,955 bars →
  2007) top-ups when the provider quota reset.
- **Phase 5 remains blocked on operator credentials** (see below). Everything
  in this plan that can run without operator secrets is now implemented.
- **Blocked on operator:** `ANTHROPIC_API_KEY` (root `.env`, then
  `docker restart trading-ai`) to activate Claude as validator; Telegram
  credentials (`TELEGRAM_BOT_TOKEN` etc.) for staleness alerts, daily briefs,
  news briefs, and approval of weekly-review config proposals; MT5 demo
  credentials for the bridge re-backfill and Phase 5. Optional:
  `ALPHA_VANTAGE_API_KEY` to enable the Alpha Vantage headline branch of
  workflow B (env access already enabled on the n8n container).

*Build plan · ai-trading-platform · original validation numbers from 2026-07-28
runs, refreshed 2026-08-13 in Phase 1 · risk defaults from
`apps/api/src/config/defaults.ts`*
