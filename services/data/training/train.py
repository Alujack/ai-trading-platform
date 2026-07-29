"""Train the LightGBM directional classifier on a built dataset.

Three things here exist specifically because the ported xaubot model got them
wrong, and each is worth more than any hyperparameter:

1. **Chronological split with purge + embargo.** A label at bar *t* peeks up to
   `horizon` bars forward, so the tail of the training set overlaps the start of
   the test set. Without purging, the model is scored on outcomes it partly saw.
   Upstream used a plain 80/20 cut (and in one script, a *shuffled*
   `train_test_split` on time series).

2. **Uniqueness weighting.** Consecutive triple-barrier labels overlap almost
   completely — on 15min with horizon 240, ~107k rows carry only a few hundred
   independent observations. Weighting each sample by how little its label span
   is shared (López de Prado's average uniqueness) stops the model from reading
   the same event thousands of times and calling it confirmation.

3. **A baseline to beat.** Accuracy against a 3-class problem is meaningless on
   its own: upstream reported 66.25% as if it were a win rate when the majority
   class alone scored 57.9%. We always print majority-class accuracy alongside.

Usage:
    python -m training.train --symbol XAUUSD --timeframe 15min
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from training.features import FEATURE_NAMES
from training.labels import HOLD, LONG, SHORT

DATA_DIR = Path(__file__).resolve().parent / "data"
MODEL_DIR = Path(__file__).resolve().parent / "models"

CLASS_NAMES = {SHORT: "SHORT", HOLD: "HOLD", LONG: "LONG"}

DEFAULT_PARAMS: dict = {
    "objective": "multiclass",
    "num_class": 3,
    "learning_rate": 0.05,
    # Deliberately conservative: the effective sample size after uniqueness
    # weighting is far smaller than the row count, so capacity is the main
    # overfitting lever here.
    "num_leaves": 31,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
    "seed": 42,
}


def average_uniqueness(bar_idx: np.ndarray, span_end: np.ndarray) -> np.ndarray:
    """López de Prado average uniqueness for overlapping label spans.

    Concurrency c_t = how many label spans cover bar t; a sample's uniqueness is
    the mean of 1/c_t over its own span. Computed with a difference array so it
    stays O(n + range) instead of O(n * horizon).
    """
    if len(bar_idx) == 0:
        return np.ones(0)
    lo = int(bar_idx.min())
    hi = int(max(span_end.max(), bar_idx.max()))
    width = hi - lo + 2

    diff = np.zeros(width, dtype=np.int64)
    starts = (bar_idx - lo).astype(np.int64)
    ends = (np.maximum(span_end, bar_idx) - lo + 1).astype(np.int64)
    np.add.at(diff, starts, 1)
    np.add.at(diff, ends, -1)
    conc = np.cumsum(diff)[:-1]
    conc[conc < 1] = 1

    inv = 1.0 / conc
    cum = np.concatenate(([0.0], np.cumsum(inv)))
    lengths = np.maximum(1, ends - starts)
    return (cum[ends] - cum[starts]) / lengths


def split_purged(
    n: int, bar_idx: np.ndarray, span_end: np.ndarray, *, test_frac: float, embargo: int
) -> tuple[np.ndarray, np.ndarray]:
    """Chronological train/test split, purging train rows whose label span runs
    into the test window and embargoing a further `embargo` bars after it."""
    cut = int(n * (1.0 - test_frac))
    test_start_bar = int(bar_idx[cut])
    train_mask = np.zeros(n, dtype=bool)
    train_mask[:cut] = True
    # Purge: drop training rows whose outcome resolves at/after the test start.
    train_mask &= span_end < test_start_bar
    # Embargo: also drop rows resolving inside the embargo band before it.
    train_mask &= bar_idx < (test_start_bar - embargo)
    test_mask = np.zeros(n, dtype=bool)
    test_mask[cut:] = True
    return train_mask, test_mask


def evaluate(y_true: np.ndarray, proba: np.ndarray, r_long: np.ndarray, r_short: np.ndarray) -> dict:
    """Accuracy vs majority baseline, plus the metric that actually matters:
    net R of the trades the model would have taken at a few thresholds."""
    pred = proba.argmax(axis=1)
    acc = float((pred == y_true).mean())
    counts = np.bincount(y_true, minlength=3)
    majority = float(counts.max() / max(1, counts.sum()))

    out: dict = {
        "accuracy": acc,
        "majority_baseline": majority,
        "edge_over_majority": acc - majority,
        "thresholds": {},
    }
    conf = proba.max(axis=1)
    for thr in (0.40, 0.50, 0.60):
        take = (pred != HOLD) & (conf >= thr)
        if not take.any():
            out["thresholds"][f"{thr:.2f}"] = {"trades": 0}
            continue
        r = np.where(pred[take] == LONG, r_long[take], r_short[take])
        wins = int((r > 0).sum())
        gross_w = float(r[r > 0].sum())
        gross_l = float(-r[r < 0].sum())
        out["thresholds"][f"{thr:.2f}"] = {
            "trades": int(take.sum()),
            "win_rate": wins / int(take.sum()),
            "expectancy_r": float(r.mean()),
            "total_r": float(r.sum()),
            "profit_factor": (gross_w / gross_l) if gross_l > 0 else float("inf"),
        }
    return out


def train_one(
    X: np.ndarray, y: np.ndarray, w: np.ndarray, params: dict, rounds: int = 600
):
    import lightgbm as lgb

    ds = lgb.Dataset(X, label=y, weight=w, feature_name=list(FEATURE_NAMES))
    return lgb.train(params, ds, num_boost_round=rounds)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", required=True)
    p.add_argument("--test-frac", type=float, default=0.25)
    p.add_argument("--rounds", type=int, default=600)
    p.add_argument("--save", action="store_true", help="persist the fitted model")
    args = p.parse_args()

    path = DATA_DIR / f"{args.symbol}_{args.timeframe}.npz"
    d = np.load(path, allow_pickle=True)
    X, y = d["X"], d["y"].astype(int)
    bar_idx, span_end = d["bar_idx"], d["span_end"]
    r_long, r_short = d["r_long"], d["r_short"]
    n = len(y)

    uniq = average_uniqueness(bar_idx, span_end)
    embargo = int(np.median(span_end - bar_idx))
    tr, te = split_purged(n, bar_idx, span_end, test_frac=args.test_frac, embargo=embargo)

    print(f"=== {args.symbol} {args.timeframe} ===")
    print(f"  samples          {n:,}")
    print(f"  mean uniqueness  {uniq.mean():.4f}  -> effective N ~ {uniq.sum():,.0f}")
    print(f"  embargo          {embargo} bars")
    print(f"  train / test     {tr.sum():,} / {te.sum():,}  (purged {n - tr.sum() - te.sum():,})")

    model = train_one(X[tr], y[tr], uniq[tr], DEFAULT_PARAMS, rounds=args.rounds)
    proba = model.predict(X[te])
    m = evaluate(y[te], np.asarray(proba), r_long[te], r_short[te])

    print(f"\n  accuracy         {m['accuracy']*100:.2f}%")
    print(f"  majority baseline{m['majority_baseline']*100:.2f}%   "
          f"edge {m['edge_over_majority']*100:+.2f} pts")
    print("\n  OOS net-R by confidence threshold (this is the number that matters):")
    print(f"  {'thr':>5} {'trades':>8} {'win%':>7} {'expR':>8} {'PF':>7} {'totR':>9}")
    for thr, s in m["thresholds"].items():
        if not s.get("trades"):
            print(f"  {thr:>5} {0:>8}")
            continue
        print(f"  {thr:>5} {s['trades']:>8} {s['win_rate']*100:>6.1f}% "
              f"{s['expectancy_r']:>+8.4f} {s['profit_factor']:>7.2f} {s['total_r']:>+9.1f}")

    imp = sorted(zip(FEATURE_NAMES, model.feature_importance("gain")),
                 key=lambda kv: -kv[1])[:12]
    print("\n  top features by gain:")
    for name, g in imp:
        print(f"    {name:24s} {g:,.0f}")

    if args.save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        stem = MODEL_DIR / f"{args.symbol}_{args.timeframe}"
        model.save_model(str(stem) + ".txt")
        (Path(str(stem) + "_metrics.json")).write_text(json.dumps(m, indent=2))
        print(f"\n  saved {stem}.txt")


if __name__ == "__main__":
    main()
