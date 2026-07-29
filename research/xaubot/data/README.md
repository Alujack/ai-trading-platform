# The training corpus does not live here

This directory held 35 Git-LFS pointer stubs — ~130-byte text files, not data.
They were deleted rather than resolved. Both reasons matter:

**1. They were the wrong venue.** Upstream trained on Kaggle XAUUSD bars. We
execute on Exness through `services/mt5bridge`. Training on a different venue
than you trade is a silent train/live drift source — different ticks, different
spreads, different session edges. `services/data/backfill_mt5.py` exists
specifically to eliminate that, and says so in its docstring.

**2. Resolving them would have re-created the trap.** A `git lfs pull` here
would drop several hundred MB of Kaggle bars into the repo and make it easy for
someone to train on them by accident.

## Where the real corpus is

Postgres, `Candle` + `Indicator`, sourced from the same broker feed we execute
on. Current XAUUSD coverage:

| TF | bars | from | indicators |
|---|---|---|---|
| 1min | 109,116 | 2026-04-16 | 100% |
| 5min | 91,801 | 2025-05-01 | 100% |
| 15min | 107,462 | 2022-06-01 | 100% |
| 60min | 27,069 | 2022-06-01 | 100% |

Depth is broker-limited and differs sharply per timeframe — probe before
trusting a target, as `backfill_mt5.py` warns. This is the single most important
fact about model quality here: the 15min and 60min models have ~4 years to learn
from, the 1min model has ~3 months, and the measured edge in
`services/data/training/models/*_metrics.json` follows that ordering exactly.

## Rebuilding

```bash
# extend history (idempotent; upserts)
python backfill_mt5.py --symbol XAUUSD --timeframe 15min --start 2022-06-01
python indicator_calculator.py --full      # backfill deliberately skips this

# build (features, labels) -> .npz
python -m training.build_dataset --symbol XAUUSD --timeframes 15min
```

`build_dataset.py` aborts if any label class exceeds 60% of the set
(`MAX_CLASS_SHARE_PCT`) — the xaubot failure mode caught in code rather than
discovered in a backtest.
