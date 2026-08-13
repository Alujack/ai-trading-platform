# EURUSD Validation — 2026-08-13 (Plan 10, Phase 4)

Decision record for the first Phase 4 expansion candidate. Raw artifacts in
[validation/2026-08-13/eurusd/](validation/2026-08-13/eurusd/).

## Why EURUSD was the candidate

The 2026-07-28 XAUUSD work noted `ict_sweep_mss` "looked promising on EURUSD
but undersized" — +R on only 39 OOS trades. Plan 10 Phase 4 requires the full
gauntlet on deep data before any pair earns a slot.

## Data

TwelveData UTC-pinned backfill (MT5-bridge route still blocked — no broker
credentials), weekend-filtered at fetch time, indicators recomputed over the
full series:

| Series | Bars | Span |
|---|---|---|
| EURUSD 60min | 40,303 | 2020-03-10 → 2026-08-13 |

Methodology identical to the XAUUSD re-validation: `walkforward.py`, rolling
1000-bar IS / 300-bar OOS folds (130 folds), fixed default params, costs ON,
regime gate ON.

## Results — fails at every cut

| Window | Folds | Trades | Expectancy | PF |
|---|---|---|---|---|
| Full WF-OOS (2020-03 → 2026-08) | 130 | 205 | **−0.207R** | **0.69** |
| Era 2020-03 → 2024-05 | 85 | 131 | −0.130R | — |
| Era 2024-06 → now (gold's good era) | 45 | 74 | **−0.343R** | — |
| Era 2025-06 → now (last 12 months) | 24 | 39 | −0.267R | — |

Win rate 34.1%, total −42.5R, OOS max drawdown 53.8R, 12 consecutive losses.
Only 43 of 130 folds were profitable.

Notably, the regime era that carries the XAUUSD edge (mid-2024 onward) is
EURUSD's **worst** stretch — the mechanism's tailwind on gold does not
transfer. The July +R read on 39 trades was small-sample noise, the same
failure mode as XAUUSD 15min's one-regime artifact.

The Monte-Carlo random baseline and cost-stress steps were not run: both
exist to qualify a *positive* edge, and there is none — the strategy loses
money at 1× costs before any stress.

## Decision (gates from plan 10)

- Expectancy > 0: **fail** (negative in every window).
- PF ≥ 1.3: **fail** (0.69).
- Beats ≥95% of random baselines: **not applicable** — nothing to qualify.

**EURUSD is NOT enabled.** No Strategy scoping, no RiskConfig rows, no
ingestion config were added. Per the plan ("GBPUSD / USDJPY only after EURUSD
earns its slot"), Phase 4 forex expansion stops here: **the desk stays
XAUUSD-60min-only.** Re-run the gauntlet only on materially different data
(broker bars via the MT5 bridge) or a materially different strategy
configuration — not on hope.
