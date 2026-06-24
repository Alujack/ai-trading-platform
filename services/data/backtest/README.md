# Backtester

Replays stored candles + indicators through the **unchanged** strategy modules
(`strategies/`) and assigns each strategy an expectancy, win rate, profit factor,
and max drawdown — the gate every strategy must clear before paper or live use
(CLAUDE.md: *"Backtest every strategy before live use"*).

## Run it

```bash
cd services/data
./.venv/bin/python backtester.py --list            # what history is stored
./.venv/bin/python backtester.py                   # all strategies, default TFs, costs on
./.venv/bin/python backtester.py --timeframes 15min 60min 1min --out ./bt_out
./.venv/bin/python backtester.py --no-costs        # isolate raw edge (optimistic)
./.venv/bin/python backtester.py --strategies scalp_ema --symbols BTCUSD --risk 1
```

Key flags: `--balance`, `--risk` (% per trade), `--start/--end` (ISO),
`--spread/--slippage/--commission-bps` (cost overrides), `--out` (writes
`trades.csv` + `summary.json`).

## What it models (deliberately conservative)

- Signal computed on a **closed** bar; entry fills at the **next** bar's open (no look-ahead).
- Exits checked **intrabar** against each later bar's high/low.
- When one bar spans both stop and target, **stop is assumed first** (worst case).
- **Spread + slippage + commission** charged on every trade (retail estimates — calibrate to your broker).
- Position size = `riskAmount / stopDistance` on **compounding** equity (same rule as the live risk engine).
- One open position at a time per (strategy, symbol); honours each candidate's `cooldown_ms`.

## Layout

| file | role |
|------|------|
| `engine.py` | pure bar-replay + trade simulation + cost model (no DB) |
| `metrics.py` | trades → expectancy / win rate / PF / max DD / streaks |
| `loader.py` | pulls candle+indicator history from TimescaleDB |
| `report.py` | text rendering + blunt go/no-go verdict |
| `../backtester.py` | CLI entry point |
| `../tests/test_backtest.py` | unit tests (synthetic bars, no DB) |

## Caveats

- Results on **<30 trades are not statistically meaningful** — the report says so per-row.
- Spread is baked into fills and cannot be separated from gross P&L in the metrics; only the commission component is reported as `total_costs`.
- This is in-sample on whatever history is loaded. **Always validate out-of-sample** before sizing up.
