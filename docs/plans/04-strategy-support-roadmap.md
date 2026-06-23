# Strategy-Support Roadmap — Phases 4–9

_Companion to `research/forex-strategy-survey.md`. Grounds the four strategy families (trend-following, mean reversion, breakout/volatility, carry) in the actual codebase, and lays out a phased plan to support them properly._

This roadmap follows the same format as `00-audit.md` and the Phase 1–3 plans: each section has **Why**, **Steps** (with real file references), and **Done when**. It assumes Phases 1–3 are landed (data + indicators + AI service all working).

---

## Where the code stands today

A quick audit of what exists, mapped to the strategy families in the research.

| Area | Current state | File |
|---|---|---|
| Strategy logic (Python) | RSI(14)<30 & close>EMA200 → LONG; RSI>70 & close<EMA200 → SHORT. SL 1.5×ATR, TP 3×ATR. | `services/data/strategy_detector.py` |
| Strategy logic (TS) | EMA20>EMA50 **and** RSI∈[40,55] **and** ATR>5 → LONG only, then AI + risk gate. | `apps/api/src/signals/signalGenerator.ts` |
| Indicators | RSI(14), EMA20/50/200, ATR(14). | `services/data/indicator_calculator.py` |
| Risk engine | Fixed-% sizing, daily-loss 3%, max-DD 10%, min RR 2, 30-min news window. Persists `RiskLog`. | `apps/api/src/risk/riskEngine.ts` |
| Execution | Paper trades; exits on static TP/SL only; weekly AI journal review. | `apps/api/src/execution/paperTrading.ts` |
| Performance | winRate, totalPnL, maxDrawdown, averageRR over live trades. | `apps/api/src/services/performance.ts` |

### The four biggest gaps (in priority order)

1. **Two strategies that quietly disagree.** `strategy_detector.py` fires on RSI extremes (a *mean-reversion-in-trend* idea); `signalGenerator.ts` fires on an EMA-trend pullback with RSI 40–55 (a *trend* idea). They use different rules, different directions (Python does both, TS is long-only), and only the TS path goes through the AI + risk gate. There is no single definition of "a strategy," so adding three more families would multiply the confusion. **This must be unified first.**
2. **No regime detection.** The research's single highest-leverage recommendation. ADX isn't even computed. Every strategy currently runs blind to whether the market is trending, ranging, or volatile — which is exactly the condition that decides whether each family wins or blows up.
3. **No backtester.** `CLAUDE.md` rule says *"Backtest every strategy before live use"* — but nothing simulates historical P&L, walk-forward, costs, or computes Sharpe/Sortino/drawdown. `performance.ts` only measures already-executed live trades. You cannot honestly add strategies without this.
4. **Risk + exits aren't strategy-aware.** Sizing is fixed-% (not ATR/volatility-normalized), there's no correlation cap (long EUR/USD + long GBP/USD is a doubled USD bet — flagged in the research), and exits are static TP/SL only (no ATR trailing stop for trend-following, no time-stop for mean reversion).

The phases below close these in dependency order.

---

## Phase 4 — Unify the strategy framework

**Why:** You can't "support the strategies" until there is one definition of what a strategy *is*. Today the logic is split across a Python detector and a TS generator with different rules and gates. We want every strategy — current and future — to emit candidate signals through one pipeline that always passes through AI validation and the risk engine (per the `CLAUDE.md` rule "Risk engine must be called before any trade execution").

**Steps**

1. Define a single strategy contract. Recommended home: the Python side (`services/data/`), since indicators already live there and backtesting (Phase 7) will want to call strategies directly over historical bars.
   ```python
   # services/data/strategies/base.py
   class Strategy(Protocol):
       name: str
       regimes: set[Regime]          # which regimes it's allowed to trade
       def evaluate(self, window: BarWindow) -> list[SignalCandidate]: ...
   ```
   A `SignalCandidate` carries `direction`, `entry`, `stop`, `target`, `confidence`, `reasoning`, and `strategy_name`.
2. Add a `Strategy` + `StrategyConfig` table to Prisma so parameters (RSI thresholds, ATR multipliers, channel periods) are data, not magic numbers:
   ```prisma
   model Strategy {
     id        String  @id @default(cuid())
     name      String  @unique           // "trend_ema", "meanrev_rsi", "breakout_donchian", "carry"
     enabled   Boolean @default(false)
     regimes   String                    // CSV/JSON of allowed regimes
     params    Json                      // strategy-specific parameters
     createdAt DateTime @default(now())
   }
   ```
3. Tag every signal with its origin: add `strategyName String?` to the `Signal` model so performance and backtests can be sliced per strategy.
4. Migrate the two existing strategies into the new shape as the first two registered strategies (`meanrev_rsi` from `strategy_detector.py`, `trend_ema` from `signalGenerator.ts`), preserving their current rules. **No behavior change yet — just structure.**
5. Route both through the existing AI + risk gate. Right now only `signalGenerator.ts` calls `/analyze/validate-signal` and `validateTrade()`. Make the Python-detected candidates flow through the same gate before they become `PENDING` signals (either by having the detector POST candidates to an API endpoint, or by moving the gate call into a shared service).

**Done when:** `SELECT name, enabled FROM "Strategy"` lists the registered strategies; both legacy strategies produce signals tagged with `strategyName`; and every new signal — regardless of which strategy produced it — has an AI score and a `RiskLog` row before it reaches `PENDING`.

**Effort:** ~1–1.5 days (mostly refactor, low risk if behavior is held constant).

---

## Phase 5 — Indicators + regime detection

**Why:** The research is blunt that the strategies fail in opposite conditions, so **regime detection is the highest-value single feature**. It also requires indicators you don't compute yet (ADX for trend strength, Bollinger Bandwidth for the squeeze/volatility state, Donchian channels for breakouts, MACD for trend confirmation).

**Steps**

1. Extend `_compute()` in `services/data/indicator_calculator.py` with the indicators each family needs:
   - **ADX(14) + +DI/−DI** — trend strength, the core regime input.
   - **Bollinger Bands(20, 2σ)** → store `bbUpper`, `bbLower`, `bbWidth` (bandwidth = the squeeze signal).
   - **Donchian channel(20 and 55)** → `donchianHi`, `donchianLo` (breakout + Turtle).
   - **MACD(12,26,9)** → `macd`, `macdSignal`, `macdHist` (trend confirmation).
   `pandas_ta_classic` (already imported) has `adx`, `bbands`, `donchian`, and `macd`, so this is additive.
2. Add the matching nullable columns to the `Indicator` model in `prisma/schema.prisma` and the `UPSERT_SQL` in the calculator.
3. New module `services/data/regime.py` implementing a classifier per `(symbol, timeframe)`:
   - `ADX > 25` → **TRENDING** (favor trend / breakout)
   - `ADX < 20` **and** narrow Bollinger Bandwidth → **RANGING** (favor mean reversion)
   - Bandwidth above a rolling high / ATR spiking → **VOLATILE** (favor breakout; suppress mean reversion & carry)
   Thresholds live in config, not hardcoded.
4. Persist the latest regime per `(symbol, timeframe)` (small `Regime` table or a Redis key, as the Phase-2 plan did for structure) and expose `GET /api/regime?symbol=...&timeframe=...`.
5. **Gate strategies on regime** in the Phase-4 pipeline: a strategy only evaluates when the current regime is in its `regimes` set. Log skips as `reason="wrong_regime"`.
6. Surface the regime as a pill in the Navbar (reuse the Phase-2 trend-pill pattern), e.g. `EURUSD 60min · RANGING`.

**Done when:** the `Indicator` table has ADX/Bollinger/Donchian/MACD populated; `GET /api/regime` returns a label; and the signal log shows strategies being skipped when the regime doesn't match.

**Effort:** ~2 days.

---

## Phase 6 — Implement the four strategy families

**Why:** This is the actual "support the strategies" request. With the framework (Phase 4), indicators + regime gating (Phase 5) in place, each family is now a small, testable module with researched default parameters. Build them long **and** short (the current TS generator is long-only, which forfeits half of every trend).

**Steps** — one strategy module each under `services/data/strategies/`, defaults from the research report:

1. **`trend_ema` (trend-following).** EMA 9/21 (or 50/200) crossover, gated to `TRENDING` (ADX>25). Confirm with MACD. Entry on crossover; exit handed to the ATR trailing stop (Phase 8). Regime: TRENDING. Expect ~30–45% win rate, high reward:risk.
2. **`meanrev_rsi` (mean reversion).** RSI(14) 30/70 **or** Bollinger-band touch with close back inside; target = middle band / RSI 50; hard stop ~1–2×ATR beyond the extreme; **time-based exit** if no reversion in N bars. Regime: **RANGING only** — this is the strategy that "stays overbought longer than you can stay solvent" in a trend, so the regime gate is the safety mechanism.
3. **`breakout_donchian` (breakout/volatility).** Turtle-style: enter on 20-period Donchian break, exit on 10-period reverse break; 2×ATR ("2N") hard stop; ATR-normalized unit sizing (Phase 8). Optional London-session opening-range variant. Add a volume/Bandwidth-expansion confirmation filter to cut fakeouts. Regime: TRENDING or VOLATILE.
4. **`carry` (carry trade) — design spike, not full build yet.** This one is structurally different: the edge is the interest-rate differential, not a chart pattern, and it needs data you don't ingest (per-currency policy rates / broker swap points) plus a much longer holding horizon. Scope this phase to: (a) a data feed for rate differentials/swaps, (b) a simple ranked carry signal (long high-yield vs. short funding currency) **gated to low-volatility/risk-on regimes**, and (c) explicit crash-risk caps given its steep negative skew (the Aug-2024 yen unwind is the cautionary tale in the research). Flag it as experimental until the risk controls in Phase 8 exist.

Each module ships with unit tests (mirror the existing `riskEngine.test.ts` / `paperTrading.test.ts` style) asserting entry/exit logic on hand-built bar fixtures.

**Done when:** all four are registered in the `Strategy` table; the three technical families produce backtestable signals (Phase 7) on historical data; and each is disabled-by-default in live/paper until it passes a backtest.

**Effort:** ~1 day per technical strategy (3 days), plus ~2–3 days for the carry data feed + spike.

---

## Phase 7 — Backtesting engine (the missing `CLAUDE.md` requirement)

**Why:** `CLAUDE.md` mandates *"Backtest every strategy before live use"* and *"Paper trade before real money,"* but there is no backtester. Without it, adding strategies is guesswork, and the research is emphatic that un-cost-adjusted, in-sample results are dangerously optimistic (a backtest Sharpe of ~2.0 often becomes ~1.0–1.4 live).

**Steps**

1. New engine `services/data/backtest.py` that replays stored `Candle` history bar-by-bar through a registered `Strategy`, feeding it only data available up to each bar (**no look-ahead** — the research's most fundamental backtesting error). Reuse `SignalCandidate` → simulated fill → exit logic so backtest and live share the same code path.
2. **Model costs honestly:** apply per-symbol spread + slippage on every fill, and stress at 1.5–2× your best cost estimate. Frequent, small-edge strategies live or die here.
3. **Walk-forward + out-of-sample:** split history into in-sample (parameter selection) and untouched out-of-sample windows; support rolling walk-forward. Resist tuning on the full set (data-snooping).
4. **Metrics:** extend `performance.ts` (or a Python equivalent) beyond winRate/PnL/maxDD/avgRR to add **Sharpe, Sortino, profit factor, and Calmar** — the research stresses judging by risk-adjusted return and drawdown, never win rate alone (mean reversion and carry hide tail risk behind high win rates).
5. Persist results: `Backtest` + `BacktestTrade` tables (strategy, symbol, timeframe, window, params, metrics) so runs are comparable and idempotent (reuse the deterministic signal-hash trick already in `strategy_detector.py`).
6. CLI + an API route to launch a run and a dashboard card to view results per strategy.

**Done when:** `python backtest.py --strategy trend_ema --symbol EURUSD --timeframe daily --from ... --to ...` returns Sharpe/Sortino/maxDD/profit-factor with costs applied; results are stored and viewable; and a strategy cannot be enabled for paper trading until it has a passing out-of-sample backtest on record.

**Effort:** ~3–4 days (this is the centerpiece; do it carefully).

---

## Phase 8 — Strategy-aware risk and exits

**Why:** The current risk engine is solid but strategy-agnostic. The research calls for volatility-normalized sizing, correlation control, skew-aware caps, and exit styles matched to each family. These are the controls that turn the high-win-rate-but-fat-tailed strategies (mean reversion, carry) from "looks great until it doesn't" into something survivable.

**Steps**

1. **ATR/volatility-normalized sizing** in `riskEngine.ts`: add a Turtle-style "unit" mode where size is set so a 1×ATR move ≈ a fixed % of equity, alongside the existing fixed-% mode. This keeps dollar-risk constant across pairs and volatility regimes (already half-there since signals carry ATR-based stops).
2. **Correlation cap:** track open exposure per base/quote currency and reject or shrink a trade that would double a currency bet (the long EUR/USD + long GBP/USD = doubled USD example from the research). Start with a static correlation/exposure map; refine later.
3. **Per-strategy risk budgets + skew caps:** let each `Strategy` carry a max-concurrent-positions and max-portfolio-risk allocation, with tighter caps on negative-skew strategies (mean reversion, carry). Enforce in `validateTrade()`.
4. **Exit logic in `paperTrading.ts`** — today `evaluateExit()` only does static TP/SL. Add:
   - **ATR trailing stop** for `trend_ema` and `breakout_donchian` (trail by N×ATR; the research's standard trend exit).
   - **Time-based exit** for `meanrev_rsi` (close after N bars if the mean isn't reached).
   - **Donchian reverse-break exit** (10-period) for the Turtle breakout.
5. Keep persisting every decision to `RiskLog` (extend it with the new checks) so the journaling rule stays satisfied.

**Done when:** sizing mode is selectable per strategy; a correlated second position is demonstrably shrunk/blocked with a logged reason; and open trend/breakout trades trail their stops while mean-reversion trades close on the time-stop.

**Effort:** ~2–3 days.

---

## Phase 9 — Decay monitoring & live validation

**Why:** The research is clear that FX edges decay (simple technical rules went from profitable in the 1970s–80s to roughly zero by the 1990s; signal effectiveness is estimated to erode ~5–10%/year). A strategy that backtests well will quietly stop working, so monitoring must be built in, not bolted on.

**Steps**

1. **Rolling performance monitor:** recompute each enabled strategy's live/paper Sharpe and drawdown on a rolling window (extend the existing weekly-review cron in `paperTrading.ts`). Alert when rolling Sharpe drops below a floor or drawdown breaches a limit; auto-disable on a hard breach.
2. **Rolling stationarity check for mean reversion:** run a rolling Augmented Dickey-Fuller test on the pairs `meanrev_rsi` trades; flag/disable a pair when it stops being mean-reverting (the research's key caveat — stationarity is not permanent).
3. **Regime-drift alert:** warn when a strategy is trading in a regime different from the one it was validated in.
4. Feed all of this into the weekly AI journal review so the existing `/analyze/journal-review` summarizes strategy health alongside behavioral patterns.

**Done when:** disabling triggers fire on synthetic degraded data in tests; the weekly review output includes a per-strategy health line; and a pair failing the rolling ADF test is automatically pulled from `meanrev_rsi`.

**Effort:** ~2 days.

---

## Suggested order & rationale

1. **Phase 4 (framework)** — nothing else is clean until "a strategy" has one definition and one gated pipeline.
2. **Phase 5 (regime)** — highest single-feature value per the research; unblocks correct gating.
3. **Phase 7 (backtester)** — pull this *before* building all four families if you want to validate as you go; it's the `CLAUDE.md` gate for going live. (Phases 6 and 7 can interleave: build one strategy, backtest it, repeat.)
4. **Phase 6 (strategies)** — implement families, validating each through the backtester.
5. **Phase 8 (risk + exits)** — required before any of the negative-skew strategies (mean reversion, carry) trade real size.
6. **Phase 9 (decay)** — ongoing safety net once strategies are live in paper.

## What this roadmap deliberately defers

- **Live brokerage execution / real money** — gated behind passing backtests + a paper-trading track record, per `CLAUDE.md`. Out of scope here.
- **Full carry-trade production build** — Phase 6 only spikes it; the data feed and crash-risk controls are non-trivial.
- **ML/ederived signals** — the four classical families and honest backtesting come first; the research shows even simple rules need rigorous validation before anything fancier is worthwhile.

## How this ties back to the research

Every phase traces to a finding in `research/forex-strategy-survey.md`: regime detection (Phase 5) because the families fail in opposite conditions; honest, cost-aware, walk-forward backtesting (Phase 7) because in-sample results overstate live performance; ATR-normalized sizing and correlation/skew caps (Phase 8) because the high-win-rate strategies hide tail risk; and decay monitoring (Phase 9) because FX edges erode over time. The platform's edge is in disciplined regime-aware execution and risk control — not in any single strategy.
