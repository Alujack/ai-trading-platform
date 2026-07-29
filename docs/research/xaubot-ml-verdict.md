# Verdict: the xaubot model (`ml_xau`) has no edge

**Date:** 2026-07-29
**Subject:** `strategies/ml_xau.py`, serving `services/data/models/lightgbm_real_26features.onnx`
**Upstream claim:** 66.2% win rate · 1.96 profit factor · 3,780% return
**Verdict: FAILED.** Do not enable. Left unregistered-in-practice as a documented negative result.

---

## The result

Backtested on XAUUSD 1min — its native and only meaningful timeframe — over
109,112 stored bars from the broker we actually execute on (Exness via
`services/mt5bridge`), with the platform's retail cost model applied.

| | `ml_xau` | `ml_xau_random` |
|---|---|---|
| trades | 2,652 | 2,689 |
| **win rate** | **34.5%** | **34.5%** |
| **expectancy** | **−0.116 R** (−$3.64) | **−0.117 R** (−$3.59) |
| profit factor | 0.84 | 0.81 |
| max drawdown | 96.6% | 96.8% |
| per-trade Sharpe | −4.26 | −4.32 |

`ml_xau_random` is the geometry- and timing-matched control: it fires on exactly
the bars the model clears its threshold on, with the identical ATR stop and RR
target, and flips a coin for direction.

**The model and the coin flip are indistinguishable.** Same win rate to one
decimal. Expectancy differs by 0.001 R — noise. Whatever the model is doing when
it picks a side, it is worth nothing over choosing at random inside the same
frame, and the frame itself loses money after costs.

This is precisely the question `baseline_mc.py` was built to ask, and it is the
one a positive backtest can never answer on its own.

### At the advertised threshold it does not trade at all

At upstream's documented 0.55 confidence threshold: **0 signals across 109,106
bars.** SHORT probability peaks at 0.476 and LONG is never predicted. The 34.5%
figures above required dropping the threshold to 0.35 to make it fire — so the
numbers are already a concession, not a hostile reading.

---

## Why it fails

Four defects, all verified rather than inferred:

**1. It is a 16-feature model wearing a 26-feature interface.** Ten of its
inputs (`mtf_0..mtf_9`) are hard-coded `0.0` — placeholders for a Transformer
that upstream never shipped. Pinned in
`tests/test_ml_features.py::test_ml_xau_mtf_inputs_are_dead`.

**2. It is bound to the price regime it trained at.** `ema_10/20/50`, `tr` and
`atr_14` are raw price-scale features. Trained on 2022-2024 gold near $1,900;
2026 gold trades near $4,000, so every tree split on those features saturates.
`tests/test_ml_features.py::test_features_are_scale_free` encodes the general
rule this violates.

**3. Permanent train/live skew on the EMAs.** Upstream trained with pandas
`ewm` over the full series; inference runs over a 100-bar window. The EMAs are
path-dependent, so `ema_50` sits ~0.11 price units away from the value the model
was fit against — measured, not estimated. This cannot be fixed from our side:
closing it means changing what the frozen ONNX binary is fed.

**4. Its training venue is not our execution venue.** Kaggle bars vs Exness.
Different ticks, spreads and session edges — the drift source
`services/data/backfill_mt5.py` exists to eliminate.

### A latent bug found while validating

`ml_xau._ema` returned a silent `0.0` for any series longer than ~3,550 bars
(span 10; ~7,050 at span 20). `decay[i]` underflows, the division yields `inf`,
`nan_to_num` converts the resulting `NaN` to a plausible-looking zero. Nothing
raised.

No live path reached it — both `evaluate` and `backtest/engine.py` pass exactly
`lookback` (100) bars — so the verdict above is unaffected. It now raises rather
than corrupting, guarded by
`test_ml_xau_ema_guard_raises_instead_of_zeroing`.

---

## What this does and does not settle

**Settled:** the vendored ONNX model has no demonstrable edge on our data, at any
threshold that lets it trade. It should not be enabled, and the upstream
performance claims should not be relied on for anything.

**Not settled:** whether *any* model on this feature contract can work. The
answer to that lives in `services/data/training/`, which is the platform's own
pipeline built from this post-mortem, and in `strategies/ml_platform.py`, which
serves what it produces. Those get the same treatment — backtest, walk-forward,
and a Monte-Carlo against `ml_platform_random` — before anything is enabled.

Early evidence there points the same direction on short timeframes and tracks
data depth almost exactly:

| TF | broker depth | edge over majority class |
|---|---|---|
| 15min | 2022-06+ (~4 yr) | **+0.0019** (PF 1.22 @ 0.50) |
| 5min | 2025-05+ (~1 yr) | −0.058 |
| 1min | 2026-04+ (~3 mo) | −0.019 |

The one timeframe with years of history is the only one that is not negative,
and even it is thin enough that the random-baseline Monte-Carlo genuinely
decides it. `ml_xau` is a 1min model — the row with the least data behind it.

---

## Reproducing

```bash
# zero trades at the advertised threshold
docker exec trading-worker python backtester.py \
  --strategies ml_xau ml_xau_random --symbols XAUUSD --timeframes 1min

# the numbers in the table above
docker exec trading-worker python backtester.py \
  --strategies ml_xau ml_xau_random --symbols XAUUSD --timeframes 1min \
  --params '{"minConfidence": 0.35}'

# formal significance test
docker exec trading-worker python baseline_mc.py \
  --target ml_xau --baseline ml_xau_random \
  --target-params '{"minConfidence": 0.35}' \
  --baseline-params '{"minConfidence": 0.35}' \
  --symbols XAUUSD --timeframes 1min --seeds 30
```

The Monte-Carlo is a formality here — the single-draw control already matches
the model to within 0.001 R — but it is the documented gate, so it gets run.
Its p-value will land near 0.5; that is the expected result for a strategy whose
selection adds nothing.

Full provenance of the vendored source: `research/xaubot/IMPORT_NOTES.md`.
