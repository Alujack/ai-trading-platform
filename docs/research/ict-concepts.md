# ICT (Inner Circle Trader) Concepts — Research Report

**Prepared:** 2026-06-24
**Scope:** Concepts explained · codeable implementation rules · evidence & criticism
**Audience:** ai-trading-platform strategy/AI engineering

> **Disclaimer:** This is an educational/engineering reference, not financial advice. Nothing here is a recommendation to trade. Treat every rule below as a *hypothesis to backtest*, per the project rule "Backtest every strategy before live use."

---

## 1. What ICT is, and where it comes from

ICT ("Inner Circle Trader") is a discretionary, price-action trading methodology popularized by **Michael J. Huddleston**, a US-based trader focused on index futures (NASDAQ, S&P 500, Dow) and forex. The brand emerged in the early 2000s and is now distributed mainly through a large free YouTube catalogue (the brand reports ~1.3–1.8M subscribers).

The central premise: **markets are not random and are not driven simply by retail supply/demand.** Instead, ICT argues that algorithmic and institutional participants ("smart money") deliver price in order to engineer and harvest **liquidity** — i.e. they push price to where retail stop-losses cluster, fill large orders against that liquidity, then move price to its real objective. Huddleston frames this delivery engine as the **Interbank Price Delivery Algorithm (IPDA)**.

Everything in the ICT toolkit is, in effect, an attempt to answer three questions:

1. **Where is the liquidity** (where are stops resting)?
2. **What is the institutional reference price** (where will price react)?
3. **When** is delivery most likely (time-of-day windows)?

This is the key differentiator from classical technical analysis: ICT puts **time** and **liquidity intent** at the center, not indicators.

---

## 2. Core concepts (the vocabulary)

### Market structure
- **Swing high / swing low:** local pivots used to define structure.
- **BOS (Break of Structure):** price breaks the most recent swing high (uptrend) or swing low (downtrend) → **trend continuation** signal.
- **CHoCH (Change of Character):** the first break *against* the prevailing structure (e.g. a downtrend starts making a higher high) → potential **reversal**.
- **MSS (Market Structure Shift):** a CHoCH accompanied by **displacement** (a strong, fast move), treated as a higher-conviction reversal signal.

### Liquidity
- **Buy-side liquidity (BSL):** resting buy stops *above* swing highs / equal highs / round numbers.
- **Sell-side liquidity (SSL):** resting sell stops *below* swing lows / equal lows.
- **Liquidity sweep / raid / "stop hunt":** price spikes through a high/low to trigger those stops, then reverses. This sweep is treated as *confirmation of manipulation* before a real move.

### Price-Delivery (PD) Arrays — the institutional reference levels
- **Order Block (OB):** the last down-close candle before a strong up-move (bullish OB), or the last up-close candle before a strong down-move (bearish OB). Marks where smart money is presumed to have loaded orders; price often reacts on return.
- **Fair Value Gap (FVG) / imbalance:** a 3-candle pattern where candle 1's wick and candle 3's wick do **not** overlap, leaving an untraded gap created by candle 2's fast move. ICT expects price to return ("rebalance") into the gap.
- **Breaker Block:** a *failed* order block — price traded through it, so it flips polarity and acts as S/R from the other side.
- **Mitigation Block:** an order block that *held* (price respected it without closing past its extreme); used for continuation.
- **Rejection Block:** zone defined by the wicks (not bodies) of a rejection.
- **Liquidity Void:** a thin, gappy region of the chart with little trading; high probability of being filled.

### Premium / Discount (the "PD Array" framing)
- Take a significant swing range and apply a 50% equilibrium line.
- **Premium** = upper half (favor selling); **Discount** = lower half (favor buying).
- **OTE (Optimal Trade Entry):** the **0.62–0.79 Fibonacci retracement** of a displacement leg, with **0.705** as the "sweet spot." Best when it overlaps an OB or FVG (confluence).

### Time-based concepts
- **Killzones** (times when high-probability setups cluster, EST):
  - Asian: ~20:00–00:00 (builds the range)
  - London: ~02:00–05:00 (often sweeps the Asian range)
  - New York AM: ~07:00–10:00 (continues or reverses London)
  - *(London Close ~10:00–12:00 is also used by some.)*
- **Silver Bullet:** a 1-hour window (commonly **10:00–11:00 NY**) where price retraces into a morning FVG and continues in the session direction.
- **Judas Swing:** a false move at session open designed to trap traders before the real move.
- **Power of Three (AMD):** every session/timeframe cycles through **Accumulation → Manipulation (the Judas swing) → Distribution** (the real directional leg).

### Cross-market & bias
- **SMT Divergence (Smart Money Technique):** two correlated assets (e.g. ES vs NQ, EURUSD vs GBPUSD) disagree at a key level — one makes a higher high, the other fails to — signaling a likely reversal.
- **Daily bias:** a higher-timeframe directional lean (from draw-on-liquidity, prior day high/low, structure) that gates the direction of intraday setups.
- **Displacement:** an aggressive, low-retracement move signaling institutional participation; it's what "validates" an MSS and typically *creates* the FVG you then trade.

### A canonical ICT trade (how the pieces fit)
1. Establish **daily bias** (say, bullish).
2. Wait for a **killzone**.
3. Price **sweeps SSL** (raids sell-side liquidity below a low) — manipulation.
4. **MSS/CHoCH up** with **displacement**, leaving an **FVG** and/or a **bullish OB**.
5. Enter on retrace into the **FVG/OB/OTE** zone (discount).
6. Stop below the swept low; **target the opposing liquidity** (BSL above).

---

## 3. Implementation spec — codeable rules

These translate the discretionary concepts into deterministic detectors suitable for `services/ai` (detection) and the strategy engine. Parameters are starting points to be optimized in backtest, not gospel. Notation uses the project glossary (candle = OHLCV bar, RR = risk/reward, ATR = average true range).

### 3.1 Swing points & market structure
```
swing_high(i, k): high[i] == max(high[i-k : i+k])      # fractal, k≈2..3
swing_low(i, k):  low[i]  == min(low[i-k : i+k])
```
- Maintain ordered lists of confirmed swing highs/lows.
- **BOS_up:** close > last_confirmed_swing_high. **BOS_down:** close < last_confirmed_swing_low.
- **CHoCH:** first BOS in the opposite direction of the current structure label.
- **MSS:** CHoCH where the breaking leg qualifies as **displacement** (see 3.4).
- State machine: `{bullish, bearish, neutral}` updated on each confirmed break.

### 3.2 Liquidity pools
- **Equal highs/lows:** cluster swing highs (or lows) within `tol = c * ATR` (c≈0.1–0.25). Two+ pivots ≈ a liquidity pool.
- Tag levels: prior day/week high & low, session high/low, round numbers.
- **Sweep detection:** `high[i] > pool_high AND close[i] < pool_high` (bull-trap sweep) — i.e. wick takes the level but body rejects. Mirror for sell-side.

### 3.3 Order Block
- **Bullish OB:** find the last candle with `close < open` (down-close) immediately preceding an up-move that produces displacement / a BOS_up within `N` candles (N≈1–3).
  - OB zone = `[low, high]` (or `[open, low]`) of that candle.
- **Bearish OB:** mirror (last up-close before displacement down).
- **Validity filters:** require the subsequent move to (a) break structure and/or (b) create an FVG; optionally require the OB to be unmitigated (price hasn't returned yet).
- **Invalidation:** candle close beyond the far side of the OB.

### 3.4 Displacement (gate for "institutional" moves)
- `body[i] = |close - open|`; `range[i] = high - low`.
- Flag displacement if `body[i] >= D * ATR(period)` (D≈1.5–2.0) **and** `body/range >= 0.5`, optionally over a 1–3 candle run.
- Used to validate MSS and to qualify OB/FVG creation.

### 3.5 Fair Value Gap (3-candle imbalance)
- **Bullish FVG** at candle `i`: `low[i+1] > high[i-1]` → gap = `(high[i-1], low[i+1])`.
- **Bearish FVG** at candle `i`: `high[i+1] < low[i-1]` → gap = `(high[i+1], low[i-1])`.
- Track **fill state:** unfilled → partially filled (price enters) → mitigated (price fully traverses). Many implementations use the **50% of the gap (consequent encroachment)** as the reaction level.
- Filters: only keep FVGs born from a displacement candle; optionally only those aligned with daily bias.

### 3.6 Breaker / Mitigation blocks
- **Breaker:** an OB that gets violated (close beyond far side) **and** then price returns; flip its role (bullish→resistance, etc.).
- **Mitigation:** OB that price retests but does **not** close beyond; trade continuation in original OB direction.

### 3.7 Premium/Discount & OTE
- Define active range from a chosen swing pair `(A=low, B=high)`.
- `equilibrium = 0.5`. Premium = price > eq; Discount = price < eq.
- **OTE zone:** fib retracement of the displacement leg, `0.62 ≤ r ≤ 0.79`, sweet spot `0.705`.
- Highest conviction when OTE ∩ OB ∩ FVG ∩ (correct premium/discount) all overlap → a **confluence score**.

### 3.8 Time filters (killzones)
- Store sessions in **exchange/EST tz**; tag each candle with its killzone.
- Gate signal generation to enabled killzones; add a dedicated **Silver Bullet** window flag (10:00–11:00 NY).
- Beware DST: compute windows from a tz library, not fixed UTC offsets.

### 3.9 SMT divergence
- Maintain synced series for a correlated pair (e.g. NQ vs ES).
- At a shared pivot time: if asset A makes higher-high while B makes lower-high (or fails the high) → bearish SMT; mirror for bullish. Emit as a **confirmation flag**, not a standalone signal.

### 3.10 Signal assembly, entry/exit, and platform wiring
A composable rule (pseudocode for the strategy engine):
```
bias        = daily_bias()                      # HTF gate
in_kz       = killzone_active(now)
swept       = liquidity_sweep_against(bias)     # manipulation
shifted     = MSS_in_direction(bias)            # displacement-validated
zone        = nearest(FVG | OB | OTE) in discount(bias)
confluence  = count(active PD arrays at zone)   # scoring

if bias and in_kz and swept and shifted and price_in(zone) and confluence>=2:
    entry = zone.reaction_level                 # e.g. OB proximal / FVG 50%
    stop  = beyond(swept_extreme) + buffer*ATR
    risk  = |entry - stop|
    target= opposing_liquidity_level()          # draw on liquidity
    if (|target-entry| / risk) >= MIN_RR:       # e.g. MIN_RR = 2.0
        emit_signal(entry, stop, target, reasoning=confluence_breakdown)
```
Wiring to **this codebase** (per `CLAUDE.md`):
- **Detection** lives naturally in `services/ai` (FastAPI) and/or `services/data` workers over TimescaleDB candles.
- **Risk engine must be called before any execution** — pass `entry/stop/target/size` for validation; reject if RR or exposure fails.
- **Every signal must be journaled with reasoning** — emit the confluence breakdown (which PD arrays fired, killzone, bias, sweep level) as the `reasoning` payload.
- **Backtest before live; paper before real** — see §4 for how to test this *honestly*.
- Practical metrics: ATR for stop buffers/displacement; EMA can proxy the HTF bias gate; RSI is *not* part of ICT but could be an orthogonal filter.

### 3.11 Hard engineering caveats
- **Look-ahead bias** is the #1 killer: swing points/OBs/FVGs are only *confirmed* after `k` future candles. Your backtester must reveal them at confirmation time, not formation time. (Note your memory item *backtest-regime-parity* — same discipline applies here.)
- **Repainting:** structure labels can flip; freeze the label used for each historical decision.
- **Subjectivity → parameterization:** every "obvious" discretionary choice (which swing, which OB) becomes a parameter; resist overfitting them.
- **Fees/slippage/spread**: ICT setups often target tight stops; costs materially change edge.

---

## 4. Evidence & criticism — does it actually work?

### What is genuinely useful
- **Liquidity-aware thinking** (stops cluster at obvious highs/lows; breakouts there often fail) is consistent with well-documented stop-hunt / failed-breakout behavior.
- **Time-of-day structure** is real: volatility and session opens genuinely cluster activity; restricting trading to a few windows reduces overtrading.
- **Imbalance/gap fill** has *some* empirical support — practitioner and at least one exchange-linked study report above-50% "hit rates" for FVG reactions in certain markets, though methodology and selection bias vary.
- It gives discretionary traders a **structured checklist** (bias → killzone → sweep → shift → entry zone → opposing liquidity target).

### The substantive criticisms
- **Rebranding of older ideas.** Critics note OB ≈ supply/demand zones (e.g. Sam Seiden), breaker ≈ polarity-flip S/R, liquidity raid ≈ failed breakout / "turtle soup," MSS ≈ break-and-retest. ICT's contribution is mostly *framing and time-integration*, not new mechanics.
- **No verified track record.** There is no independently audited, profitable live track record for the methodology's originator. Public, accountable attempts underperformed: a 2016 \$10k→\$1M public challenge and the 2024 Robbins Cup both ended in failure/blown accounts. Revenue is largely content/brand-driven, not documented trading profits.
- **Unfalsifiable, post-hoc tendency.** With ~20 overlapping PD arrays and multiple time windows, almost any move can be "explained" after the fact. High flexibility → low falsifiability → easy to fool yourself in discretionary review.
- **Hard to systematize.** Prop-firm operators report ICT-trained traders show good *structure awareness* but struggle with *consistent execution and risk management*; the edge claims rarely survive as mechanical rules without heavy curation.
- **Selection bias in "proof."** Testimonials and curated chart examples dominate; there's a shortage of large-sample, pre-registered, out-of-sample tests.

### Honest bottom line
ICT is **valuable as a lens** (liquidity, time, institutional intent) and **unproven as a turnkey edge.** The concepts are real phenomena dressed in proprietary vocabulary; whether *your* codification of them produces positive expectancy is an empirical question only your backtests can answer — and only if those backtests avoid look-ahead/repainting and include realistic costs.

### How to test it rigorously on this platform
- Encode **one** concept at a time as a mechanical rule (start with FVG-fill or OB-reaction) so you can attribute edge.
- Demand a meaningful sample: aim for **200+ trades**, **profit factor > ~1.5**, positive expectancy, before believing anything.
- Use **walk-forward / out-of-sample** splits and **regime gating parity** between backtest and live (you already track this).
- Compare against a **dumb baseline** (e.g. random entries with the same stop/target geometry and same killzone filter) — ICT must beat the geometry, not just the RR math.
- Track results in the trade journal with full reasoning so degradation is diagnosable.

---

## 5. Recommendations for ai-trading-platform

1. Treat ICT as a **feature library** feeding the AI/strategy layer (OB, FVG, sweep, MSS, killzone flags, premium/discount, SMT) rather than a monolithic "ICT strategy."
2. Build each detector with explicit **confirmation lag** and unit tests on hand-labeled charts.
3. Gate every assembled signal through the **risk engine** and **journal the confluence reasoning** (already mandated by `CLAUDE.md`).
4. Backtest individually, then as a weighted **confluence score**; paper-trade the survivors.
5. Keep a **skeptic's baseline** in every experiment to prove the concepts add value beyond stop/target geometry and time-of-day.

---

## Sources

- [Key ICT Concepts — TradeZella](https://www.tradezella.com/learning-items/key-ict-concepts)
- [ICT Trading Strategy: Complete Guide — LiteFinance](https://www.litefinance.org/blog/for-beginners/trading-strategies/ict-trading-strategy/)
- [What Are the Inner Circle Trading Concepts — FXOpen](https://fxopen.com/blog/en/what-are-the-inner-circle-trading-concepts/)
- [ICT Trading Basics — GrandAlgo](https://grandalgo.com/guides/ict-trading-basics)
- [ICT Order Block Explained — innercircletrader.net](https://innercircletrader.net/tutorials/ict-order-block/)
- [Most Important ICT Concepts (Complete List) — innercircletrader.net](https://innercircletrader.net/tutorials/most-important-ict-concepts-to-conquer-market-complete-list/)
- [ICT Optimal Trade Entry (OTE) — ictkillzone.com](https://www.ictkillzone.com/ict-ote)
- [ICT Power of 3 (AMD) — innercircletrader.net](https://innercircletrader.net/tutorials/ict-power-of-3/)
- [ICT Mitigation Block Explained — innercircletrader.net](https://innercircletrader.net/tutorials/ict-mitigation-block-explained/)
- [ICT Breaker Block Trading — innercircletrader.net](https://innercircletrader.net/tutorials/ict-breaker-block-trading/)
- [ICT SMT Divergence Explained — innercircletrader.net](https://innercircletrader.net/tutorials/ict-smt-divergence-smart-money-technique/)
- [Fair Value Gap Trading Strategy — TrendSpider](https://trendspider.com/learning-center/fair-value-gap-trading-strategy/)
- [Fair Value Gap Trading: Strategies, Backtesting & Risk — ForexTester](https://forextester.com/blog/fair-value-gap/)
- [Studying the Effectiveness of Qi's Fair Value Gap (white paper) — Euronext](https://www.euronext.com/sites/default/files/2019-04/Qi%20Euro%20Stoxx%20600%20White%20Paper.pdf)
- [Is ICT Trading Legit? — Phidias Propfirm](https://phidiaspropfirm.com/education/is-ict-legit)
- [Who Is Inner Circle Trader? Michael J. Huddleston — Writofinance](https://www.writofinance.com/inner-circle-trader-ict-michael/)
- [Backtesting Trading Strategies: Complete Guide — TradeZella](https://www.tradezella.com/blog/backtesting-trading-strategies)
