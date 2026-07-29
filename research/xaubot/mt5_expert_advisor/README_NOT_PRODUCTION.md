# These Expert Advisors are reference material. Do not run them.

The `.mq5` files in this directory are a **second, competing execution path**
that this platform does not use and must not be pointed at a live account
alongside it.

## Why

The EAs do their own position sizing and risk management inside the MetaTrader
terminal. `CLAUDE.md` states two rules this violates:

> - Risk engine must be called before any trade execution
> - Every trade signal must be journaled with reasoning

An EA executing from inside MT5 satisfies neither. It never reaches
`apps/api/src/risk/riskEngine.ts`, and it writes no `Signal` or `Trade` row, so
nothing it does appears in the journal, the dashboard, or the performance
numbers.

## The failure mode if you run both

`apps/api/src/execution/executionPolicy.ts` enforces portfolio-level caps — max
open trades, max open risk, per-currency exposure. It computes those from the
`Trade` table. **EA positions are invisible to it.**

So with an EA running on the same account, the platform sizes its next trade as
though the EA's exposure does not exist, and the EA does the same in reverse.
Two independent sizers, each blind to the other, both drawing on one margin
pool. Every cap in the system silently reports a number lower than the truth.

## The supported path

```
strategy_runner.py
  → POST /api/signals/candidate      (gate.ts: AI validation + risk engine)
  → decideExecution                  (OFF / AUTO / CONFIRM, portfolio caps)
  → paperTrading.ts | liveTrade.ts
  → exnessBroker.ts
  → services/mt5bridge/app.py        (FastAPI, MetaTrader5 lib, one lock)
  → MT5 terminal
```

The bridge drives MT5 from *outside*, which is what lets every order pass
through the risk engine and land in the journal first.

## What is still useful here

`XAUUSD_NeuralBot_Single.mq5` and `XAUUSD_NeuralBot_Ensemble.mq5` document
upstream's 6-layer hybrid validation filter (spread, RSI, MACD, ADX, ATR range,
multi-timeframe EMA) and its feature construction in MQL5. That is worth reading
if you are reimplementing any of it as a platform strategy — which is the right
way to adopt it.

See `../IMPORT_NOTES.md` for the full provenance of this vendored tree.
