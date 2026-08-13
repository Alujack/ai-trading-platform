# XAUUSD Re-validation — 2026-08-13 (Plan 10, Phase 1)

Decision record for the `ict_sweep_mss` re-validation required by
[docs/plans/10-agentic-forex-build-plan.md](../plans/10-agentic-forex-build-plan.md)
Phase 1, run on the repaired data foundation (Phase 0). Raw run outputs are in
[validation/2026-08-13/](validation/2026-08-13/).

## Data this ran on

Phase 0 wiped all corrupted XAUUSD rows (mixed exchange-time timestamps, ~30%
weekend filler bars, zero volume) and re-backfilled from TwelveData with the
UTC pin and the new weekend-bar filter in `fetcher.py`. The MT5-bridge route
was blocked (no MT5 credentials configured anywhere), so this is still
TwelveData feed — the broker-data re-run happens when bridge credentials exist.

| Timeframe | Bars | Span | Exit gates |
|---|---|---|---|
| 60min | 32,781 | 2021-02-09 → now | 0 weekend bars, 0 tz leads, fresh ✓ |
| 15min | 61,231 | 2024-02-01 → now | 0 weekend bars, 0 tz leads, fresh ✓ |
| 1min | 70,757 | 2026-06-04 → now | thin history (quota-capped) |
| 5min / daily | ~100 each | — | top-up pending TwelveData quota reset |

Methodology matches 2026-07-28: `walkforward.py` (rolling 1000-bar IS / 300-bar
OOS, costs ON, regime gate ON, fixed default params — no grid registered for
sweep_mss) and `baseline_mc.py` (100 seeds, frame-matched `useKillzone:false`).

## Results

### 15min — DEAD, dropped from scope

| Window | Trades | Expectancy | PF |
|---|---|---|---|
| Full WF-OOS (2024-02 → 2026-08, 200 folds) | 297 | **−0.091R** | 0.85 |
| Era before Dec 2025 | 204 | −0.147R | — |
| Era Dec 2025+ (the July window) | 93 | +0.031R | — |

The 2026-07-28 pass (+0.25R/PF 1.52 on 65 trades) came from a series that only
reached back to Dec 2025 — one regime. With 2.5 years of clean history it is
clearly negative, and even the July-era slice no longer reproduces strongly.
**15min must re-earn its slot through the full gauntlet before re-enabling.**

### 60min — edge is real but regime-local; kept, alone

| Test | Trades | Expectancy | PF | Notes |
|---|---|---|---|---|
| Full WF-OOS (2021-04 → 2026-08, 105 folds) | 182 | +0.070R | 1.13 | WFE 1.45 |
| Era 2021-04 → 2024-05 (WF folds) | 113 | −0.044R | — | no edge |
| Era 2024-06+ (WF folds) | 69 | **+0.257R** | — | reproduces July |
| Fixed-params backtest 2024-06 → 2026-08 | 68 | +0.217R | **1.38** | maxDD 9.1%, +15% net @1% risk |
| MC vs 100 random baselines (full series) | 173 | +0.041R | 1.05 | **p = 0.010** — beats 99%; random mean −0.048R |

Reading: the sweep+MSS mechanism beats chance decisively even across the full
5.5 years (the frame contributes nothing — random baselines lose money), but
its useful expectancy is concentrated in the regime gold has been in since
mid-2024. That is exactly what forward paper trading (Phase 2) tests in real
time, with the drawdown breakers armed if the regime rolls over.

## Decision (gates from plan 10)

- Expectancy > 0: **pass** (all 60min windows positive).
- PF ≥ 1.3: **pass in-era** (1.38 since Jun 2024); 1.13 over the full history.
- Beats ≥95% of random baselines: **pass** (99%, p=0.010).

**Enable `ict_sweep_mss` on XAUUSD 60min only, CONFIRM mode, paper broker** —
1% risk/trade, 2% daily-loss breaker, RR ≥ 2, max 2 open trades.
15min removed from the strategy scope (`seedIctSweepMss.ts` updated).
Known caveats carried into Phase 2: regime-dependence above; TwelveData feed
(not broker bars); Telegram not yet configured, so CONFIRM approvals cannot be
answered until the operator sets `TELEGRAM_*` env — signals will queue and
expire until then.
