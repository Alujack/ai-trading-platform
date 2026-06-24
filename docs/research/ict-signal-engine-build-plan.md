# ICT Daily Signal Engine — Build Plan

**Prepared:** 2026-06-24
**Goal:** A service (NOT an MT4/MT5 EA) that runs each ICT strategy independently, draws each strategy on its own chart, emits a **separate signal + reason per strategy**, then aggregates into **one high-conviction signal per day on EURUSD** targeting ~100 pips. Delivered to a **dashboard + Telegram/Discord**, with **semi-auto one-click execution**.

> **Reality check (read first).** EURUSD's *average daily range* is often only ~60–90 pips. A single trade hitting **+100 pips every day is not realistic** — some days no qualifying setup will appear, and a 100-pip take-profit won't always fill. The right design goal is: **fire only on high-conviction days, skip the rest, and aim for 100 pips when conditions justify it.** A system that takes zero trades on a bad day is working correctly. This is an engineering plan, not financial advice or a profit guarantee — everything must be backtested and paper-traded first (per `CLAUDE.md`).

---

## 0. The signal spec & the money math

- **Instrument:** EURUSD. **Cadence:** at most 1 actionable signal/day. **Target:** 100 pips (with realistic partials/trailing — see §6).
- **Sizing math (your example, confirmed):** 0.01 lot = 1,000 units (micro-lot). Pip value ≈ **$0.10/pip**. So **100 pips × $0.10 = $10.00**. ✔
  - Stop matters too: if SL is e.g. 50 pips, risk at 0.01 lot = **$5**, giving a 2:1 RR for a $10 target.
- **Position sizing should be derived from risk, not fixed lots.** Recommended: `lots = risk_$ / (stop_pips × pip_value_per_lot)`. Fixed 0.01 is fine for paper/early live, but build the risk-based formula so you can scale later.

---

## 1. Data feed (free)

Recommended primary: **OANDA v20 *practice* account API** — free, gives clean EURUSD candles (M1→D) *and* order execution on the same API (perfect for the one-click execution requirement). 0.01 lot = 1,000 units maps directly.

Free alternatives behind an adapter:
- **MT5 demo + `MetaTrader5` Python package** — free candle pull from a demo terminal (this is *data only*, not an EA).
- **TwelveData free tier** (~800 req/day) or **Alpha Vantage FX** — fine for lower-frequency polling.
- **Dukascopy historical** (free tick/candle export) — best for the **backtest history**.

> Build a `DataFeed` adapter interface so the source is swappable. Use OANDA practice for live polling + execution, Dukascopy/MT5 for deep backtest history.

---

## 2. Architecture (maps to your existing stack)

```
                 ┌──────────────── services/data (Python worker) ────────────────┐
DataFeed adapter │  poll EURUSD candles (M15/H1/H4/D) → TimescaleDB (candles)     │
(OANDA practice) │  cache latest bars in Redis                                    │
                 └───────────────────────────────────────────────────────────────┘
                                          │ candles
                 ┌──────────────── services/ai (FastAPI) ────────────────────────┐
                 │  Strategy registry → run each ICT detector independently:      │
                 │    each returns { signal, direction, entry, sl, tp, reason,    │
                 │                   confidence, drawings[] }                      │
                 │  Aggregator → confluence score → ≤1 daily EURUSD signal        │
                 └───────────────────────────────────────────────────────────────┘
                                          │ signals + drawings
                 ┌──────────────── apps/api (Express/TS) ────────────────────────┐
                 │  REST/WebSocket: /strategies, /signals/today, /signal/:id      │
                 │  Risk engine gate (MANDATORY before any execute)               │
                 │  Journal every signal w/ reasoning                             │
                 │  Notifier: Telegram/Discord push                               │
                 │  Execute: one-click → OANDA order (semi-auto)                  │
                 └───────────────────────────────────────────────────────────────┘
                                          │
        apps/web (Next.js): per-strategy annotated charts • daily signal card • one-click button
```

Cron/scheduler (Redis queue or a worker tick) runs the pipeline at the **end of each killzone** (or once after the London/NY open) to produce the day's candidate signals.

---

## 3. Per-strategy modules — each draws itself & emits its own signal+reason

Each ICT strategy is an isolated **Detector** implementing one interface, so you can enable/disable, chart, and backtest them one at a time. Start with the ICT concepts already documented in `docs/research/ict-concepts.md`.

**Detector interface (Python, in `services/ai`):**
```python
class Detector:
    name: str
    timeframe: str                       # e.g. "M15", "H1"
    def evaluate(self, candles) -> DetectorResult: ...

@dataclass
class DetectorResult:
    direction: Literal["long","short","none"]
    entry: float; sl: float; tp: float
    confidence: float                    # 0..1
    reason: str                          # human-readable, journaled
    drawings: list[Drawing]              # chart annotations (see §4)
    valid_until: datetime
```

**Strategies to ship (one detector each):**

| # | Strategy | Fires when… | Its chart drawings | Example reason string |
|---|----------|-------------|--------------------|-----------------------|
| 1 | **Liquidity Sweep + MSS** | Sweep of prior day low/equal lows, then displacement MSS up (mirror for short) | swept level, sweep wick, MSS break line | "Swept Asian low 1.0832, MSS up w/ displacement → long" |
| 2 | **Order Block retest** | Price returns to a valid bullish/bearish OB after BOS | OB zone box, BOS line, entry arrow | "Bullish OB 1.0840–1.0846 retested after BOS → long" |
| 3 | **Fair Value Gap fill** | Price retraces into an unfilled FVG aligned with bias | FVG box, 50% (CE) line | "Bullish FVG 1.0838–1.0844, price at CE → long" |
| 4 | **OTE (0.62–0.79 fib)** | Retrace into 0.705 zone of displacement leg | fib retracement, OTE band | "OTE 0.705 = 1.0841 in discount → long" |
| 5 | **Silver Bullet** | 10:00–11:00 NY FVG entry in daily-bias direction | killzone shade, FVG box | "NY Silver Bullet FVG, bias long → long" |
| 6 | **Power of Three / Judas** | Session accumulation → manipulation sweep → distribution | AMD phase shading | "London Judas swing down then reversal → long" |
| 7 | **SMT Divergence** *(confirmation)* | EURUSD vs GBPUSD/DXY disagreement at a level | dual-pair markers | "EURUSD HL vs DXY LH → bullish SMT" |

Each detector also writes its own **per-strategy chart** so on the dashboard you literally see "this is what strategy X saw."

---

## 4. The drawing/chart layer

- Detectors emit structured **`Drawing`** primitives (source-of-truth, frontend-agnostic):
  ```
  Drawing = { type: "box"|"line"|"hline"|"label"|"fib"|"arrow"|"zone",
              coords: [...], color, label, ttl }
  ```
- **Frontend:** Next.js page renders EURUSD candles with **TradingView Lightweight Charts** (free, MIT) and overlays the `drawings[]` for the selected strategy. One tab per strategy + an "All / Confluence" tab.
- **For Telegram/Discord:** server-side render each strategy chart to PNG (e.g. headless chart render or `mplfinance`) so the push message includes the picture, not just text.

---

## 5. Aggregator → the 1 daily signal (with reason)

1. Run all detectors for the day's candles.
2. Gate by **daily bias** + **killzone** (drop setups against HTF bias or outside windows).
3. **Confluence score** = weighted sum of agreeing detectors at the same price zone & direction (e.g. Sweep+MSS 0.3, OB 0.2, FVG 0.2, OTE 0.15, SMT 0.1, SilverBullet 0.05).
4. Emit the **single best** long/short candidate **only if** `score ≥ threshold` AND `RR ≥ 2` AND target room ≥ ~100 pips to the next opposing liquidity. Otherwise **no trade today** (this is expected and healthy).
5. The aggregate **reason** stitches each contributing detector's reason: *"Long 1.0841. Confluence 0.78: liquidity sweep of PDL + bullish OB retest + FVG CE + OTE 0.705, NY killzone, bullish SMT vs DXY. SL 1.0808 (-33p), TP 1.0941 (+100p), RR 3.0."*

**Signal schema (journaled — satisfies "every signal must be journaled with reasoning"):**
```json
{
  "id":"...", "ts":"...", "symbol":"EURUSD", "direction":"long",
  "entry":1.0841, "sl":1.0808, "tp":1.0941,
  "stop_pips":33, "target_pips":100, "rr":3.0,
  "suggested_lots":0.01, "risk_usd":3.30, "reward_usd":10.00,
  "confluence":0.78,
  "contributors":[{"strategy":"liquidity_sweep_mss","direction":"long","confidence":0.8,"reason":"..."}, ...],
  "reason":"...", "charts":["url/strategy1.png", ...], "status":"proposed"
}
```

---

## 6. Targets & the 100-pip goal (practical)

- Set TP at the **next opposing liquidity pool**; if that's <100 pips, take the trade with a smaller target rather than forcing 100.
- Recommended: **scale out** (e.g. 50% at +50p, move SL to BE, trail rest toward +100p) so "100-pip days" are captured without giving back open profit. Pure fixed +100 TP will miss many otherwise-winning trades.
- Track **expectancy in $ at 0.01 lot** so you can see the real path to your $10/day intuition across a sample, not per-day.

---

## 7. Delivery & execution

- **Telegram/Discord:** bot posts the daily signal card + per-strategy PNGs + the aggregate reason. (Telegram Bot API / Discord webhook — both free.)
- **Dashboard (Next.js):** strategy tabs with live annotated charts, a "Today's Signal" card, and a **one-click Execute** button.
- **Semi-auto execution:** button → `apps/api` → **risk engine validation (mandatory)** → OANDA practice order at suggested lots → status streamed back. Default to **practice/demo** until backtest + paper results justify live.

---

## 8. Validation (non-negotiable, per project rules)

- **Backtest each detector individually first** on Dukascopy/MT5 history — confirmation lag enforced, no repainting/look-ahead (see caveats in `ict-concepts.md` §3.11; mirror your `backtest-regime-parity` discipline).
- Then backtest the **aggregator**. Benchmarks before trusting it: **200+ trades, profit factor > ~1.5, positive expectancy**, plus a **geometry-matched random baseline** it must beat.
- **Paper-trade** the survivor for a meaningful window before any real money.

---

## 9. Phased roadmap

**Phase 0 — Foundations (week 1)**
- `DataFeed` adapter + OANDA practice connection; EURUSD candles → TimescaleDB; Redis cache. Backfill history from Dukascopy.

**Phase 1 — Detector framework + first 3 strategies (weeks 2–3)**
- Detector interface, registry, `Drawing` primitives. Implement Liquidity-Sweep+MSS, Order Block, FVG. Unit tests on hand-labeled charts.

**Phase 2 — Charts & per-strategy signals on dashboard (week 4)**
- Lightweight-Charts UI, per-strategy tabs, signal+reason cards. PNG render for messaging.

**Phase 3 — Remaining strategies + aggregator (weeks 5–6)**
- OTE, Silver Bullet, Power-of-Three, SMT. Confluence scoring → 1 daily signal. Bias & killzone gates.

**Phase 4 — Risk engine + journaling + delivery (week 7)**
- Risk-engine gate, signal journaling, Telegram/Discord push, dashboard one-click → OANDA practice order.

**Phase 5 — Backtest, paper, tune (weeks 8+)**
- Full backtests, baseline comparison, paper trading, threshold tuning. Only then consider live.

---

## 10. Open decisions to confirm later
- Primary timeframe for entries (M15 vs H1) and which HTF defines daily bias (H4/D).
- Exact confluence weights & score threshold (will be set empirically in backtest).
- Whether to keep fixed 0.01 lot or switch to risk-based sizing once paper results are in.
