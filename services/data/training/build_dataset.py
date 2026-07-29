"""Build the (features, labels) training set for a symbol/timeframe.

Reads Candle+Indicator from Postgres, builds one feature row per bar with the
SAME `features.build_feature_row` the live strategy calls, labels each bar with
the cost-aware triple-barrier simulation, and writes an .npz.

The live strategy is `strategies/ml_platform.py`. It imports `build_feature_row`
from this same module and pins its `lookback` to `features.LOOKBACK` and its
stop/target to `labels.LabelConfig`, so the trade the model was labelled on is
the trade that actually gets placed. Change the geometry in one place and you
must change it in both — `train.py`'s docstring explains what goes wrong
otherwise.

Row validity is bounded on both sides and this is load-bearing:
  * the first `LOOKBACK-1` bars have no feature window;
  * the last `horizon` bars have no room for a label to resolve, so including
    them would teach the model that unresolved trades are HOLDs.

Usage:
    python -m training.build_dataset --symbol XAUUSD --timeframes 15min 5min 1min
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from db import close_pool, init_pool
from strategies.base import IndicatorBar
from training.features import FEATURE_NAMES, LOOKBACK, N_FEATURES, build_feature_row
from training.labels import LabelConfig, class_balance, default_horizon, triple_barrier

log = logging.getLogger("data.training.build_dataset")

DATA_DIR = Path(__file__).resolve().parent / "data"

# Guard against the xaubot failure mode: a label set dominated by one direction
# produces a model that can only ever trade that way.
MAX_CLASS_SHARE_PCT = 60.0

_SQL = """
SELECT c."timestamp", c.open, c.high, c.low, c.close, c.volume,
       i.rsi, i.ema20, i.ema50, i.ema200, i.atr,
       i."bbLower", i."bbUpper", i."bbPctB", i.adx
FROM "Candle" c
JOIN "Indicator" i
  ON i.symbol = c.symbol AND i.timeframe = c.timeframe AND i."timestamp" = c."timestamp"
WHERE c.symbol = $1 AND c.timeframe = $2
ORDER BY c."timestamp"
"""


async def load_bars(pool, symbol: str, timeframe: str) -> list[IndicatorBar]:
    rows = await pool.fetch(_SQL, symbol, timeframe)
    return [
        IndicatorBar(
            timestamp=r["timestamp"], close=r["close"], open=r["open"],
            high=r["high"], low=r["low"], volume=r["volume"],
            rsi=r["rsi"], ema20=r["ema20"], ema50=r["ema50"], ema200=r["ema200"],
            atr=r["atr"], bb_lower=r["bbLower"], bb_upper=r["bbUpper"],
            bb_pctb=r["bbPctB"], adx=r["adx"],
        )
        for r in rows
    ]


def build(bars: list[IndicatorBar], timeframe: str, symbol: str) -> dict[str, np.ndarray]:
    n = len(bars)
    horizon = default_horizon(timeframe)
    cfg = LabelConfig(horizon=horizon)

    arr = lambda k: np.array(  # noqa: E731
        [float(getattr(b, k)) if getattr(b, k) is not None else np.nan for b in bars]
    )
    o, h, l, c = arr("open"), arr("high"), arr("low"), arr("close")
    atr = arr("atr")

    lab = triple_barrier(o, h, l, c, atr, cfg=cfg, symbol=symbol)

    lo = LOOKBACK - 1
    hi = n - horizon - 1  # last bar whose label had room to resolve
    if hi <= lo:
        raise SystemExit(f"{timeframe}: not enough bars ({n}) for lookback+horizon")

    X, y, ts, rl, rs, span, bidx = [], [], [], [], [], [], []
    for i in range(lo, hi + 1):
        if not lab.valid[i]:
            continue
        row = build_feature_row(bars[i - LOOKBACK + 1 : i + 1], timeframe=timeframe)
        if row is None:
            continue
        X.append(row)
        y.append(lab.label[i])
        ts.append(np.datetime64(bars[i].timestamp))
        rl.append(lab.r_long[i])
        rs.append(lab.r_short[i])
        bidx.append(i)
        # How far forward this label "used up" — the later of the two simulated
        # trades' resolutions, or the full horizon if neither resolved. Training
        # needs this: with horizon=240 on 15min, consecutive labels overlap
        # almost completely, so 107k rows are nowhere near 107k independent
        # observations. `train.py` turns these spans into uniqueness weights.
        el, es = int(lab.exit_idx_long[i]), int(lab.exit_idx_short[i])
        end = max(el if el >= 0 else i + horizon, es if es >= 0 else i + horizon)
        span.append(end)

    return {
        "X": np.asarray(X, dtype=np.float32),
        "y": np.asarray(y, dtype=np.int8),
        "ts": np.asarray(ts, dtype="datetime64[ns]"),
        "r_long": np.asarray(rl, dtype=np.float64),
        "r_short": np.asarray(rs, dtype=np.float64),
        "bar_idx": np.asarray(bidx, dtype=np.int64),
        "span_end": np.asarray(span, dtype=np.int64),
    }


async def main_async() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframes", nargs="+", default=["15min", "5min", "1min"])
    args = p.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    pool = await init_pool()
    try:
        for tf in args.timeframes:
            bars = await load_bars(pool, args.symbol, tf)
            log.info("%s: loaded %d bars", tf, len(bars))
            d = build(bars, tf, args.symbol)
            bal = class_balance(d["y"])

            print(f"\n=== {args.symbol} {tf} ===")
            print(f"  samples      {len(d['y']):,}   features {N_FEATURES}")
            print(f"  span         {d['ts'][0]} -> {d['ts'][-1]}")
            print(f"  horizon      {default_horizon(tf)} bars")
            print(f"  SHORT {bal['SHORT']:.1f}%   HOLD {bal['HOLD']:.1f}%   LONG {bal['LONG']:.1f}%")
            print(f"  mean net R   long {d['r_long'].mean():+.4f}   short {d['r_short'].mean():+.4f}")

            worst = max(bal["SHORT"], bal["HOLD"], bal["LONG"])
            if worst > MAX_CLASS_SHARE_PCT:
                print(f"  !! IMBALANCED: a class holds {worst:.1f}% (> {MAX_CLASS_SHARE_PCT}%)")

            out = DATA_DIR / f"{args.symbol}_{tf}.npz"
            np.savez_compressed(out, feature_names=np.array(FEATURE_NAMES), **d)
            print(f"  wrote        {out}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main_async())
