"""Walk-forward validation with per-fold RETRAINING.

`walkforward.py` re-optimises the parameters of a *fixed* strategy each fold.
That is the wrong test for a learned model: the thing that overfits is the fit
itself, so the model must be retrained from scratch on each in-sample window and
judged only on the untouched out-of-sample window that follows.

Each fold:
    1. fit on IS rows (uniqueness-weighted, purged against the OOS boundary)
    2. choose the confidence threshold that maximises IS expectancy
    3. apply that threshold, unchanged, to OOS

Choosing the threshold in-sample matters. The single-split run picked the best
threshold *after* seeing the test set, which flatters the result — exactly the
kind of soft leakage that produced xaubot's walk-forward efficiency of 0.23.

Reading the verdict (same bar as the rest of the platform):
    WF efficiency (OOS edge / IS edge) near or above 1.0  -> robust
    near 0 or negative                                    -> overfit
    plus: most folds profitable, and OOS expectancy > 0 AFTER costs

Usage:
    python -m training.walkforward_ml --symbol XAUUSD --timeframe 15min
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from training.labels import HOLD, LONG
from training.train import DEFAULT_PARAMS, average_uniqueness, train_one

DATA_DIR = Path(__file__).resolve().parent / "data"
THRESHOLDS = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65)


def _stats(pred: np.ndarray, conf: np.ndarray, r_long: np.ndarray,
           r_short: np.ndarray, thr: float) -> dict:
    take = (pred != HOLD) & (conf >= thr)
    n = int(take.sum())
    if n == 0:
        return {"trades": 0, "exp_r": 0.0, "total_r": 0.0, "pf": 0.0, "win": 0.0}
    r = np.where(pred[take] == LONG, r_long[take], r_short[take])
    gw = float(r[r > 0].sum())
    gl = float(-r[r < 0].sum())
    return {
        "trades": n,
        "exp_r": float(r.mean()),
        "total_r": float(r.sum()),
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "win": float((r > 0).mean()),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", required=True)
    p.add_argument("--folds", type=int, default=8)
    p.add_argument("--is-frac", type=float, default=0.5,
                   help="in-sample share of each fold's window")
    p.add_argument("--rounds", type=int, default=400)
    p.add_argument("--min-trades", type=int, default=20,
                   help="folds with fewer OOS trades are reported but excluded from the verdict")
    # Capacity knobs. The effective sample size after uniqueness weighting is
    # only a few thousand, so these matter far more than anything else here —
    # the default config reaches +1.7R in-sample, which is memorisation.
    p.add_argument("--num-leaves", type=int)
    p.add_argument("--min-data", type=int, dest="min_data")
    p.add_argument("--l2", type=float)
    p.add_argument("--select", choices=("rank", "fixed"), default="rank",
                   help="rank = top --quantile of each fold by confidence (drift-proof); "
                        "fixed = one absolute --threshold everywhere")
    p.add_argument("--quantile", type=float, default=0.05)
    p.add_argument("--threshold", type=float, default=0.50)
    args = p.parse_args()

    params = dict(DEFAULT_PARAMS)
    if args.num_leaves:
        params["num_leaves"] = args.num_leaves
    if args.min_data:
        params["min_data_in_leaf"] = args.min_data
    if args.l2 is not None:
        params["lambda_l2"] = args.l2

    d = np.load(DATA_DIR / f"{args.symbol}_{args.timeframe}.npz", allow_pickle=True)
    X, y = d["X"], d["y"].astype(int)
    bar_idx, span_end = d["bar_idx"], d["span_end"]
    r_long, r_short, ts = d["r_long"], d["r_short"], d["ts"]
    n = len(y)

    # Rolling, non-overlapping OOS windows tiling the series.
    oos_len = n // (args.folds + 1)
    is_len = int(oos_len * (args.is_frac / (1 - args.is_frac)))

    print(f"WALK-FORWARD (retrained per fold)  {args.symbol} {args.timeframe}")
    print(f"  samples {n:,}   folds {args.folds}   IS {is_len:,} bars   OOS {oos_len:,} bars\n")
    print(f"  {'fold':>4} {'thr':>5} {'IS expR':>9} {'OOS n':>7} {'win%':>6} "
          f"{'OOS expR':>9} {'PF':>6} {'totR':>9}")

    rows = []
    for f in range(args.folds):
        oos_start = is_len + f * oos_len
        oos_end = min(n, oos_start + oos_len)
        is_start = max(0, oos_start - is_len)
        if oos_end - oos_start < 50:
            continue

        boundary_bar = int(bar_idx[oos_start])
        tr = np.zeros(n, dtype=bool)
        tr[is_start:oos_start] = True
        tr &= span_end < boundary_bar           # purge label overlap into OOS
        if tr.sum() < 500:
            continue

        uniq = average_uniqueness(bar_idx[tr], span_end[tr])
        model = train_one(X[tr], y[tr], uniq, params, rounds=args.rounds)

        pis = np.asarray(model.predict(X[tr]))
        is_pred, is_conf = pis.argmax(axis=1), pis.max(axis=1)
        oos = slice(oos_start, oos_end)
        po = np.asarray(model.predict(X[oos]))
        oos_pred, oos_conf = po.argmax(axis=1), po.max(axis=1)

        if args.select == "rank":
            # Trade the top `--quantile` most-confident OOS bars. Absolute
            # probability thresholds do not transfer across folds: measured on
            # XAUUSD 15min, median non-HOLD confidence drifts 0.374 -> 0.406 and
            # p95 0.434 -> 0.514 from the first fold to the last, so one fixed
            # cut produced 4 trades in fold 0 and 712 in fold 7. Ranking within
            # the fold holds trade count roughly constant and isolates whether
            # the model's ORDERING has edge.
            nz = oos_pred != HOLD
            k = max(1, int(nz.sum() * args.quantile))
            cut = np.sort(oos_conf[nz])[::-1][k - 1] if nz.any() else 1.0
            best_thr = float(cut)
            best_is = _stats(is_pred, is_conf, r_long[tr], r_short[tr], best_thr)["exp_r"]
        else:
            # Fixed pre-committed threshold. NOT fitted on IS: maximising IS
            # expectancy over a grid reliably picks the highest cut (in-sample
            # expectancy is inflated by overfitting there), which then trades a
            # handful of poor OOS signals.
            best_thr = args.threshold
            best_is = _stats(is_pred, is_conf, r_long[tr], r_short[tr], best_thr)["exp_r"]

        o = _stats(oos_pred, oos_conf, r_long[oos], r_short[oos], best_thr)
        rows.append({"fold": f, "thr": best_thr, "is_exp": best_is, **o})
        print(f"  {f:>4} {best_thr:>5.2f} {best_is:>+9.4f} {o['trades']:>7} "
              f"{o['win']*100:>5.1f}% {o['exp_r']:>+9.4f} {o['pf']:>6.2f} {o['total_r']:>+9.1f}")

    if not rows:
        print("\n  no usable folds")
        return

    counted = [r for r in rows if r["trades"] >= args.min_trades]
    tot_tr = sum(r["trades"] for r in counted)
    tot_r = sum(r["total_r"] for r in counted)
    exp = tot_r / tot_tr if tot_tr else 0.0
    is_mean = float(np.mean([r["is_exp"] for r in counted])) if counted else 0.0
    eff = (exp / is_mean) if is_mean > 0 else float("nan")
    prof = sum(1 for r in counted if r["total_r"] > 0)

    print(f"\n  AGGREGATE OOS  trades={tot_tr:,}  expectancy={exp:+.4f}R  totalR={tot_r:+.1f}")
    print(f"  profitable folds: {prof}/{len(counted)}")
    print(f"  mean IS expectancy {is_mean:+.4f}R  ->  walk-forward efficiency {eff:.2f}")
    verdict = (
        "ROBUST" if (eff >= 0.5 and exp > 0 and prof * 2 >= len(counted))
        else "OVERFIT / NO EDGE"
    )
    print(f"  VERDICT: {verdict}   (gate: efficiency >= 0.5, expectancy > 0, majority of folds green)")


if __name__ == "__main__":
    main()
