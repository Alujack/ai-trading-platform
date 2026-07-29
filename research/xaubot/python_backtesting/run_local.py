"""
Local smoke-run of the backtesting path — works on a fresh clone with no LFS data.

The repo's own entry point (run_backtest.py) cannot run on a fresh clone:
  * it looks for python_training/models/lightgbm_real_26features.onnx, which is
    .gitignore'd and therefore absent (the only copy is MT5_XAUBOT/Files/),
  * it defaults to a 1.13 M-bar / 3-year run, which takes hours at ~1 800 bars/s,
  * every print() uses non-cp1252 glyphs, so it dies on a default Windows console.

This script fixes all three so you can verify the pipeline works end to end.

Usage:
    set PYTHONUTF8=1
    .venv311\\Scripts\\python.exe python_backtesting\\run_local.py
    .venv311\\Scripts\\python.exe python_backtesting\\run_local.py --start 2024-01-01 --end 2024-06-30

NOTE ON THE NUMBERS: the data is synthetic (geometric Brownian motion from
prepare_data.py), not market data, because the real parquets are Git-LFS
pointers and this repo's LFS budget is exhausted. Any P/L printed below is an
artefact of the generator, not evidence of an edge. See RUN_LOCAL.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backtest_engine import XAUUSDBacktester  # noqa: E402
from prepare_data import prepare_backtest_data  # noqa: E402

# The only non-LFS copy of the 26-feature model lives in the MT5 distribution dir.
MODEL_CANDIDATES = (
    ROOT / "python_training" / "models" / "lightgbm_real_26features.onnx",
    ROOT / "MT5_XAUBOT" / "Files" / "lightgbm_real_26features.onnx",
)


def resolve_model() -> Path:
    for path in MODEL_CANDIDATES:
        if path.exists() and path.stat().st_size > 10_000:
            return path
    raise SystemExit(
        "No usable ONNX model found. Checked:\n  "
        + "\n  ".join(str(p) for p in MODEL_CANDIDATES)
        + "\n(Files under ~200 bytes are unfetched Git-LFS pointers.)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2024-02-29")
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--risk-percent", type=float, default=0.5)
    ap.add_argument("--confidence", type=float, default=0.35)
    ap.add_argument("--max-trades-per-day", type=int, default=10)
    args = ap.parse_args()

    model_path = resolve_model()
    print(f"Model: {model_path.relative_to(ROOT)}  ({model_path.stat().st_size / 1024:.0f} KB)")

    data = prepare_backtest_data(start_date=args.start, end_date=args.end)

    bt = XAUUSDBacktester(
        initial_balance=args.balance,
        risk_percent=args.risk_percent,
        confidence_threshold=args.confidence,
        max_trades_per_day=args.max_trades_per_day,
        atr_multiplier_sl=1.5,
        risk_reward_ratio=2.0,
    )
    # MTF EMA columns in prepare_data.py are copies of ema_20, so this filter is
    # meaningless on this data; run_backtest.py disables it too.
    bt.require_mtf_alignment = False
    bt.load_lightgbm_model(str(model_path))

    signals = {0: 0, 1: 0, 2: 0}
    for idx in range(100, len(data)):
        row = data.iloc[idx]
        price, ts = row["close"], row["time"]
        if bt.current_date != ts.date():
            bt.current_date = ts.date()
            bt.daily_trades = 0
        bt.update_position(price, ts)
        if bt.open_position is None and bt.daily_trades < bt.max_trades_per_day:
            signal, conf = bt.predict_lightgbm(bt.calculate_26_features(data, idx))
            signals[signal] += 1
            if signal in (0, 2) and conf >= bt.confidence_threshold:
                bt.open_trade(signal, price, ts, row.get("atr_14", 3.0))

    bt.print_summary()

    total = sum(signals.values()) or 1
    print("\nMODEL OUTPUT DISTRIBUTION")
    for cls, name in ((0, "SHORT"), (1, "HOLD"), (2, "LONG")):
        print(f"  {name:6} {signals[cls]:>8,}  ({signals[cls] / total * 100:5.2f}%)")

    feats = bt.calculate_26_features(data, len(data) - 1)
    dead = int((feats == 0).sum())
    print(f"\nFEATURE VECTOR: {dead}/26 features are zero at the final bar.")
    print("  Indices 16-25 are hard-coded to 0.0 in calculate_26_features() *and* in")
    print("  the training script, so the model is really 16 features padded to 26.")

    if bt.trades:
        tr = pd.DataFrame(bt.trades)
        worst = tr["profit"].min()
        print("\nRISK CHECK")
        print(f"  configured risk per trade : {args.risk_percent:.2f}%  "
              f"(${args.balance * args.risk_percent / 100:,.2f})")
        print(f"  worst realised trade      : ${worst:,.2f}  "
              f"({abs(worst) / args.balance * 100:.2f}% of starting balance)")
        print(f"  lot sizes used            : {np.unique(tr['lots'])}")
        print("  -> calculate_position_size() multiplies by price/100, so lots pin at the")
        print("     1.0 clamp and risk_percent has no effect. See RUN_LOCAL.md.")


if __name__ == "__main__":
    main()
