# Platform Pipeline Reference

How a scalp signal flows through THIS system, end to end, with exact paths, payloads, and commands. Use these instead of guessing. All paths are relative to repo root `ai-trading-platform/`.

## End-to-end flow

```
Candles ingested (services/data) -> Indicators computed -> Strategy emits SignalCandidate
   -> POST /api/signals/candidate (THE GATE)
        -> AI validation (POST /analyze/validate-signal on AI service)
        -> Risk engine validateTrade()  [must approve]
        -> Signal persisted (PENDING) with full reasoning
   -> executionPolicy.decideExecution(): OFF | AUTO | CONFIRM
        -> openPaperTrade() (paper)  OR  openLiveTrade() (exness)
   -> paper cron monitors OPEN trades for TP/SL -> Trade CLOSED
   -> POST /analyze/trade-review -> Journal row (grade, outcome, lesson, rMultiple)
```

## 1. Submit a scalp signal (the gate) — primary entry point

`POST /api/signals/candidate`  (Express API, default `API_PUBLIC_URL=http://localhost:4000`)

Request (camelCase JSON):
```json
{
  "strategyName": "scalp_ema",
  "symbol": "EURUSD",
  "timeframe": "5min",
  "direction": "LONG",
  "entryPrice": 1.0843,
  "stopLoss": 1.0808,
  "takeProfit": 1.0943,
  "confidence": 60,
  "reasoning": "5min uptrend: EMA20>EMA50>EMA200, pullback to EMA20 held, RSI(7) turned up from 45. Stop 1.2x ATR below entry; TP at 1:2.86 RR.",
  "clientId": "scalp-eurusd-5min-20260624T1330Z",
  "cooldownMs": 14400000,
  "aiMinScore": 45
}
```

Response:
```json
{ "status": "generated|rejected|skipped", "signalId": "sig_...", "score": 82, "reason": "optional" }
```
- `generated` (HTTP 201): AI + risk both approved; Signal persisted PENDING. Execution policy takes over.
- `rejected` / `skipped` (HTTP 200): relay the `reason` honestly. Do NOT loosen numbers and resubmit to force approval.
- `clientId` gives idempotency; `cooldownMs` prevents spamming the same setup.

## 2. Risk engine (runs inside the gate — never bypass)

`validateTrade(input)` in `apps/api/src/risk/riskEngine.ts`. Inputs include entry, stopLoss, takeProfit, accountBalance, peakBalance, todayLoss, riskPercent, upcomingNews.

Default thresholds (`apps/api/src/config/defaults.ts`, overridable via `RiskConfig` table GLOBAL/STRATEGY/SYMBOL):
- `minRR: 2`            -> reject if reward/risk < 1:2
- `dailyLossLimitPct: 3` -> reject once today's loss exceeds 3%
- `maxDrawdownPct: 10`   -> circuit breaker on equity drawdown
- `newsBeforeMin: 30` / `newsAfterMin: 30` -> no trades within +/-30 min of HIGH-impact news

Output: `{ approved: boolean, positionSize: number, reasons: string[] }`. Position size = `accountBalance * (riskPercent/100) / |entry - stopLoss|`. Let the engine size it; don't pre-compute lots to "help."

## 3. Execution (policy-controlled)

`decideExecution(signal)` in `apps/api/src/execution/executionPolicy.ts` resolves mode:
- **OFF** -> Signal stays PENDING (resumable). Also forced OFF if a circuit breaker tripped.
- **AUTO** -> immediately `openPaperTrade(signalId)` (`apps/api/src/execution/paperTrading.ts`) or `openLiveTrade(signalId)` (`apps/api/src/execution/liveTrade.ts`).
- **CONFIRM** -> creates an Approval + sends Telegram alert with Approve/Reject buttons.

Broker selection via `.env`: `BROKER=paper` (simulator) or `BROKER=exness` (`EXNESS_ENV=demo|real`, MT5 bridge at `MT5_BRIDGE_URL`). Live order: `exnessBroker.placeOrder({symbol, side, lots, stopLoss, takeProfit, clientTag})`.

Portfolio caps enforced: `PAPER_MAX_OPEN_TRADES`, max open risk %, per-currency risk %.

## 4. Read signals / journal

```
GET /api/signals          # recent signals + context
GET /api/signals/:id       # one signal
GET /api/journal           # closed-trade journal entries
```

Schemas (Prisma):
- **Signal**: symbol, timeframe, direction, entryPrice, stopLoss, takeProfit, confidenceScore, aiReasoning, strategyName, status (PENDING->ACTIVE->CLOSED/CANCELLED).
- **Trade**: signalId, entryPrice, exitPrice, positionSize, riskAmount, profitLoss, status (OPEN->CLOSED), externalOrderId, brokerFillPrice, broker.
- **Journal**: tradeId, notes, aiReview, grade (A-F), outcome (WIN/LOSS/BREAKEVEN), lesson, rMultiple.

## 5. Backtest (MANDATORY before any live use)

REST:
```
POST /api/backtests/run
{ "timeframes": ["5min","15min"], "symbols": ["XAUUSD"], "strategies": ["scalp_ema"], "balance": 10000, "risk": 1, "noCosts": false, "label": "scalp pre-live" }
GET /api/backtests/run/status
GET /api/backtests           # list runs + summary metrics
GET /api/backtests/:id        # detail + equity curves
```

CLI:
```
python services/data/backtester.py --strategies scalp_ema --symbols XAUUSD --balance 10000 --risk 1
python services/data/backtester.py --list        # available history
python services/data/backtester.py --save-db     # persist results
```
Review profit factor, win rate, max drawdown. A scalp strategy with no acceptable backtest does not go live.

## 6. Market data

- Source: Twelve Data via `services/data/fetcher.py` for XAUUSD / EURUSD / BTCUSD.
- `Candle` (TimescaleDB hypertable): open/high/low/close/volume + timestamp, unique [symbol, timeframe, timestamp]. Timeframes: 1min, 5min, 15min, 60min, daily.
- `Indicator`: rsi (RSI-14), ema20, ema50, ema200, atr — computed by `services/data/indicator_calculator.py` (pandas_ta) after each candle batch.
- Redis: latest price at `price:{symbol}`.
- NOTE: stored RSI/EMA use platform defaults (RSI-14, EMA 20/50/200). For faster scalp settings (e.g. RSI-7, EMA 5/13) compute from raw candles or pass as strategy params; see scalping-playbook.md.

## 7. Key env vars (.env — never hardcode)

```
ENABLE_PAPER_TRADING=true
PAPER_ACCOUNT_BALANCE=10000
PAPER_PEAK_BALANCE=10000
PAPER_RISK_PERCENT=1
PAPER_MAX_OPEN_TRADES=5
BROKER=paper                 # paper | exness
EXNESS_ENV=demo              # demo | real
MT5_BRIDGE_URL=http://host.docker.internal:8800
STRATEGY_TIMEFRAMES=15min,60min
STRATEGY_SYMBOLS=XAUUSD,EURUSD,BTCUSD
REGIME_GATING=true
API_PUBLIC_URL=http://localhost:4000
AI_SERVICE_URL=http://localhost:8000
```

## 8. Strategy code to learn from

- `services/data/strategies/scalp_ema.py` — the existing intraday scalp template (EMA20>EMA50 & close>EMA20 & RSI band; fixed pip SL/TP at 1:2). Best starting point for a new scalp variant.
- `services/data/strategies/base.py` — `SignalCandidate` dataclass + `Strategy` protocol + `to_payload()` (produces the camelCase gate payload above).
- `services/data/strategies/registry.py` — register new strategies here.
- `services/data/strategy_runner.py` — builds the BarWindow and POSTs candidates to the gate.
