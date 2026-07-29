# Running this repo locally

Verified on Windows 11, Python 3.11.2, 2026-07-29.

## Setup

```powershell
py -3.11 -m venv .venv311
.venv311\Scripts\python.exe -m pip install -U pip
.venv311\Scripts\python.exe -m pip install -r requirements-local.txt
```

Do **not** use the committed `.venv/`. It is checked into git (~12,700 files) and its
`pyvenv.cfg` points at `C:\Users\KRAFTLAB\...`, so it is dead on any other machine.

## Run the backtest

```powershell
$env:PYTHONUTF8 = "1"
.venv311\Scripts\python.exe python_backtesting\run_local.py
.venv311\Scripts\python.exe python_backtesting\run_local.py --start 2024-01-01 --end 2024-06-30
```

`PYTHONUTF8=1` is mandatory. Almost every script prints `✓`/`❌`/`⚠` and Windows
consoles default to cp1252, so without it you get
`UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'`.

## What does and does not run

| Path | State |
|---|---|
| `python_backtesting/run_local.py` | Runs. Synthetic data + the 26-feature ONNX model. |
| `python_backtesting/run_backtest.py` | Needs the fixes in `run_local.py` first (see below). |
| `src/*.py` pipeline (README quick start) | Cannot run. Needs `data/raw/`, which is `.gitignore`d and absent. |
| `python_training/train_real_26features.py` | Cannot run. Needs `data/raw/XAU_1m_data.csv`. |
| `python_training/*transformer*` | Needs `torch` plus LFS `.npy` arrays. |
| `python_monitoring/dashboard.py` | Needs `streamlit`, `plotly`, `MetaTrader5` + a running MT5 terminal. |
| `python_training/onnx_parity_test.py` | Needs MT5 Strategy-Tester CSV logs that are not in the repo. |

## Blocker: Git LFS budget exhausted

```
$ git lfs pull
batch response: This repository exceeded its LFS budget.
```

All 44 project LFS objects (~1.8 GB of data, plus most model binaries) are
unfetchable. Only these binaries survive, because they were committed outside LFS:

* `MT5_XAUBOT/Files/lightgbm_real_26features.onnx` (309 KB) — excluded via `.gitattributes`
* `python_training/models/*.txt` — LightGBM native text format (`hybrid_lightgbm.txt`,
  `lightgbm_balanced.txt`, `lightgbm_xauusd_smc_filtered.txt`, `lightgbm_xauusd_v1.txt`)

Everything else — all `data/processed/*`, `*.pkl`, `*.pth`, and the other `*.onnx`
files — is a ~130-byte pointer stub. `run_local.py` therefore falls back to the
synthetic generator in `prepare_data.py`, exactly as `run_backtest.py` would.

To restore reproducibility, either raise the LFS budget on the GitHub account, or
re-host the data and drop `.venv/` from LFS tracking (it is the reason 82 of the
126 LFS objects exist at all).

## Why the printed P/L is not meaningful

`run_local.py` reports 95% win rate / +167% on a 2-month run. That number is an
artefact. The reasons, in order of severity:

1. **Synthetic data.** `prepare_data.py` generates a geometric-Brownian-motion path
   seeded at 42. Over two months it drifts inside a ~$27 band. No edge can exist in
   it, so any positive result is measuring the generator.
2. **`SHORT` is an unreachable label.** In `train_real_26features.py:131-149` the
   `low <= entry-8 -> SHORT` test sits *after* the `low <= entry-4 -> HOLD` test.
   Since `low <= entry-8` implies `low <= entry-4`, the HOLD branch always breaks
   first. Measured label distribution: **99.73% HOLD, 0.27% LONG, 0.00% SHORT.**
   The shipped model consequently emits 0 SHORT signals in 44,000 predictions and
   can never learn the short side.
3. **Position sizing is dimensionally wrong.** `backtest_engine.py:396` computes
   `lots = position_size * price / 100.0 / 100.0`. The `* price` term does not
   belong. At 0.5% configured risk on $10k the result is 2.23 lots, clamped to the
   1.0 maximum, and the realised loss is **$424.82 = 4.25% of the account, 8.5× the
   configured risk**. Because it always clamps, `risk_percent` has no effect at all.
   Correct form: `lots = risk_amount / (sl_distance * 100)`.
4. **10 of 26 features are constant zero.** `calculate_26_features()` (indices
   16-25), `CalculateFeatures()` in the MQL5 EA (line 236), and the training script
   (`mtf_{i} = 0.0`) all hard-code them. The model is a 16-feature model padded to
   26, and the "multi-timeframe confirmation" in the docs does not exist anywhere.
   Python and MQL5 *are* consistent here, so this is not train/serve skew — the
   feature set was simply never implemented.
5. **Validation is bypassed.** `run_backtest.py:99-117` replaces both
   `validate_long_signal` / `validate_short_signal` calls with `if True:` under a
   `# TEMPORARY` comment. The advertised 6-layer filter never executes.
6. **The confidence threshold cannot filter.** With 3 classes the arg-max
   probability is ≥ 0.333 by construction, so the 0.35 threshold is inert.
   Observed confidences on taken trades: 0.358-0.378.
7. **No maximum holding period.** Positions are held until a *close* touches TP or
   SL — up to 8.8 days in the 2-month run, across weekends. No swap, commission,
   slippage, or gap risk is modelled, and `equity` is only marked at trade close,
   so reported max drawdown ignores open-position drawdown entirely.
8. **Indicators will not match MT5.** RSI and ADX in `prepare_data.py` use simple
   rolling means instead of Wilder smoothing, and ADX divides smoothed DM by mean
   TR. `iRSI`/`iADX` in MT5 use Wilder, so Python↔MQL5 parity — the stated purpose
   of the engine — will not hold.
9. **`run_ensemble_model_backtest()` is a stub** that returns zeros with a
   `# For now, return placeholder metrics` comment, yet
   `python_backtesting/README.md` presents `Ensemble Model: 72.3% WR | 1.78 PF |
   $5,820 profit | 892 trades` and "RECOMMENDATION: Deploy ENSEMBLE MODEL" as
   sample output. Those numbers cannot have come from this code.

## Files added by this setup

* `requirements-local.txt` — working pins; adds the missing `onnxruntime`/`pyarrow`
  and drops unused `pandas_ta`.
* `python_backtesting/run_local.py` — runnable entry point with diagnostics.
* `.gitignore` — added `.venv311/`.
* `python_training/models/lightgbm_real_26features.onnx` — copied from
  `MT5_XAUBOT/Files/`. Already `.gitignore`d, so it will not be committed.
