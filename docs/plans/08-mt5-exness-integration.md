# Plan 08 — MT5 / Exness Live Execution (demo-first)

**Goal:** Let the system place and manage real orders on an **Exness** account through **MetaTrader 5**, starting on a **demo** account and flipping to **real** with a single config change once the promotion gate is met.

**Status:** IN PROGRESS. ✅ Phase 2 (broker seam) built + tested; rest pending.

---

## 0. Why this shape (the hard constraints)

- **Exness has no REST/WebSocket trading API.** The only supported programmatic route to a retail Exness account is the official **`MetaTrader5` Python package**, which talks to a **running MT5 desktop terminal** — and that terminal/library is **Windows-only**.
- Our stack is Node + Python in Docker on macOS. We therefore introduce a small **MT5 bridge microservice** that runs on a **Windows host/VPS** alongside the MT5 terminal, and the existing API calls it over HTTP.
- The execution layer today is **100% paper** (`apps/api/src/execution/paperTrading.ts`); there is **no broker abstraction**. So step one is a clean `Broker` seam, with the current paper logic as one implementation and Exness as the other. Paper stays the default fallback.

```
apps/api (Docker, mac/linux)                 Windows VPS
┌─────────────────────────┐   HTTPS+token   ┌──────────────────────────┐
│ executionPolicy         │ ───────────────▶│ mt5-bridge (FastAPI)      │
│  └ Broker (interface)   │                 │  /health /account         │
│      ├ PaperBroker      │                 │  /symbol /order /close    │
│      └ ExnessBroker ────┼────────────────▶│  /positions               │
└─────────────────────────┘                 │     │ MetaTrader5 lib       │
                                             │     ▼                      │
                                             │  MT5 terminal → Exness     │
                                             └──────────────────────────┘
```

---

## 1. Guardrails (do not violate)

1. **Demo before real.** `EXNESS_ENV=demo` is the default; real requires an explicit env flip *and* the promotion gate (§9).
2. **Risk engine before execution** (CLAUDE.md). `ExnessBroker.placeOrder` is only ever reached *after* `gateCandidate` + `validateTrade` approve. The broker layer never bypasses the gate.
3. **Server-side SL/TP.** Every order is sent to MT5 with stop-loss and take-profit attached, so the broker closes the position even if our bridge/API is down.
4. **Idempotency.** Each order carries a deterministic client tag (`signalId`) so a retry never double-fills.
5. **Kill switch.** A single env/DB flag (`EXECUTION_MODE=OFF` / breaker) halts all live order placement instantly; in-flight positions keep their server-side SL/TP.
6. **No secrets in code or image.** MT5 login/password/server and the bridge token live in env only.

---

## 2. Prerequisites / blocking fixes (must land before REAL; should land before meaningful DEMO)

These come straight from the system evaluation — a live broker turns them from cosmetic into money-losing:

- **P0a — Candle timestamp/timezone bug.** Candles are stored ~3h ahead of true UTC. Fix the fetcher to normalize vendor time → UTC on ingest and repair stored rows. Killzone-timed entries are otherwise mistimed. (Separate task; tracked here as a dependency.)
- **P0b — Position-size correctness.** `calculatePositionSize` ([riskEngine.ts:41](../../apps/api/src/risk/riskEngine.ts#L41)) returns raw **units** via `riskAmount / stopDistance`, assuming pip value = $1/unit and quote ccy = account ccy. Correct it to use the symbol's **tick value / contract size** (from the broker) so XAUUSD and crosses size correctly. This is required for demo to produce meaningful results, not just real.
- **P0c — Breakers see open exposure.** Daily-loss/drawdown breakers currently use realized P&L only; with real fills they must mark open positions to market and use live equity (read account from the bridge).

---

## 3. Phase 1 — MT5 bridge microservice (Windows)

New service: `services/mt5bridge/` (Python, FastAPI, `MetaTrader5`). Deployed on the Windows VPS (not in the mac Docker compose).

**Endpoints (all require `X-Bridge-Token` shared secret):**
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | terminal connected? account login ok? |
| GET | `/account` | balance, equity, margin, currency, leverage |
| GET | `/symbol/{symbol}` | digits, point, **contract_size**, **volume_min/step/max**, **trade_tick_value**, current bid/ask/spread |
| GET | `/positions` | all open positions (ticket, symbol, volume, type, price_open, sl, tp, profit, our `signalId` tag) |
| POST | `/order` | open: `{symbol, side, lots, sl, tp, clientTag, deviation}` → `{ticket, fillPrice, status}` |
| POST | `/close` | close by ticket → `{ticket, exitPrice, profit}` |

**Implementation notes**
- `mt5.initialize(login=…, password=…, server="Exness-MT5Trial"|"Exness-MT5Real")`; reconnect loop + `/health` reflects connection state.
- `/order` uses `mt5.order_send` with `type_filling` and `deviation` (max slippage) appropriate to Exness; tag via `request["comment"]`/`magic` = derived from `signalId`.
- Map result `retcode` → clear error strings (requote, no money, market closed, invalid stops).
- Run as a Windows service / NSSM so it restarts with the VPS.

**Exit of Phase 1:** manually place + close a 0.01-lot EURUSD demo trade end-to-end via curl against the bridge.

---

## 4. Phase 2 — Broker abstraction in the API

In `apps/api/src/execution/`:

- `broker/types.ts` — `interface Broker { getAccount(); getSymbolSpec(symbol); placeOrder(o); closeOrder(ticket); getPositions(); }`
- `broker/paperBroker.ts` — wraps current `paperTrading.ts` fill logic (no behavior change).
- `broker/exnessBroker.ts` — HTTP client for the bridge (`MT5_BRIDGE_URL`, `MT5_BRIDGE_TOKEN`), timeouts, typed errors.
- `broker/index.ts` — `getBroker()` factory selecting on `BROKER=paper|exness` (+ `EXNESS_ENV=demo|real` for logging/labels).

No execution path changes yet — this phase is pure scaffolding + unit tests with a mocked bridge.

---

## 5. Phase 3 — Symbol + sizing correctness

- **Symbol mapping:** add a broker symbol map (mirror of `fetcher.py` `SYMBOL_MAP`) — `EURUSD→"EURUSD"`, etc. Exness is mostly unsuffixed; **verify exact names from `/symbol` / Market Watch per account type** and make it config-driven.
- **units→lots converter:** `lots = clampToStep(units / contract_size, volume_min, volume_step, volume_max)` using `/symbol` specs; reject if below `volume_min`.
- **Rewrite sizing (P0b):** risk $ ÷ (stopDistanceInTicks × tick_value) → lots, per symbol. Add tests for EURUSD, XAUUSD, BTCUSD with known specs.

---

## 6. Phase 4 — Wire order placement (DEMO, CONFIRM mode first)

- Schema: add to `Trade` — `externalOrderId String?`, `brokerFillPrice Decimal?`, `broker String?` (`paper|exness_demo|exness_real`). Migration.
- In `executionPolicy.decideExecution` → on `AUTO`/`APPROVED`, call `getBroker().placeOrder(...)` instead of (or in addition to) `openPaperTrade`. Persist `externalOrderId` + actual fill price on the `Trade`.
- **Start in `CONFIRM` mode** so the existing Telegram one-click approval ([07-control-and-telegram-confirmation.md](07-control-and-telegram-confirmation.md)) is the human gate before every real order.
- Idempotency: if a `Trade` for this `signalId` already has an `externalOrderId`, never re-send.

---

## 7. Phase 5 — Position & exit reconciliation

With Exness, **SL/TP live on the broker server**, so the broker closes positions — we reconcile, not simulate.

- For `broker=exness`, `monitorOpenTrades` polls `/positions`:
  - position still open → update unrealized P&L/equity snapshot.
  - position gone → fetch its close (deal history) → write `exitPrice`, `profitLoss`, `status=CLOSED`, then create the `Journal` (grade/R-multiple) exactly as paper does today.
- Reconcile on startup (recover state after a restart) and detect manual closes done in the terminal.

---

## 8. Phase 6 — Safety rails & observability

- **Kill switch / breaker:** `portfolioCapBlock` + breaker already gate `decideExecution`; extend the breaker to read live equity from `/account` (P0c) and to set `EXECUTION_MODE=OFF` on trip.
- **Connectivity guard:** if `/health` is unhealthy, refuse new orders (held_off), alert via Telegram; existing positions safe via server-side SL/TP.
- **Slippage/requote handling:** surface `deviation` rejections; optional re-quote retry policy.
- **Dashboard:** show broker mode (paper / exness-demo / exness-real), live account equity, open positions with broker tickets, and a prominent REAL-MONEY banner when `EXNESS_ENV=real`.
- **Weekend/market-closed:** bridge reports market state; gate skips closed markets.

---

## 9. Phase 7 — Demo forward-test, then gated flip to REAL

Run on **Exness demo** for a defined period and only then flip. The flip is `BROKER=exness`, `EXNESS_ENV=real`, real account creds — nothing else changes in code.

**Promotion gate to REAL (all required):**
1. P0a/P0b/P0c fixed and tested.
2. At least one strategy passes its own edge gate: **OOS walk-forward n≥200 + beats the geometry-matched random baseline** on correctly-timed data.
3. ≥ N weeks of **demo** forward results with live spreads/slippage that match backtest expectancy within tolerance (no nasty fill surprises).
4. Reconciliation proven: every demo trade's broker close matched our journal with correct P&L.
5. Start real at **minimum lot + reduced risk %**, `CONFIRM` mode, with the kill switch tested.

---

## 10. Env / config additions

```
# Broker selection
BROKER=paper                 # paper | exness
EXNESS_ENV=demo              # demo | real   (label + safety)

# MT5 bridge (Windows VPS)
MT5_BRIDGE_URL=https://<vps-host>:8800
MT5_BRIDGE_TOKEN=<long-random-shared-secret>

# On the bridge host only (NOT in the mac compose):
MT5_LOGIN=<account number>
MT5_PASSWORD=<password>
MT5_SERVER=Exness-MT5Trial   # demo; Exness-MT5Real for real
```

---

## 11. Risks / gotchas (the money-losers)

- **Lots vs units / contract size** — wrong conversion = 100× mis-size. Covered by P0b + tests.
- **Naive pip-value sizing** on XAU/crosses — covered by tick-value sizing.
- **Timestamp bug** — mistimed killzone entries on real fills.
- **Slippage, requotes, partial fills, weekend gaps, terminal disconnects** — none handled by the current simulated model; addressed in Phases 5–6.
- **Windows VPS is a new single point of failure** — needs auto-restart + health alerting.
- **Symbol-name mismatch** per Exness account type — must verify against the live terminal.

---

## 12. Suggested build order (incremental, each shippable)

1. ✅ Phase 2 broker seam (paper-only, no behavior change) + tests — `apps/api/src/execution/broker/` (`types.ts`, `symbols.ts`, `paperBroker.ts`, `exnessBroker.ts`, `index.ts` + 30 tests).
2. Phase 1 bridge on the Windows VPS; manual demo trade.
3. Phase 3 sizing/symbol correctness (also fixes P0b).
4. Phase 4 wire demo orders behind CONFIRM mode.
5. Phase 5 reconciliation.
6. Phase 6 safety + dashboard.
7. Phase 7 demo soak → gated real flip.

The only code that changes when going real is env. Everything builds and runs against demo first.
