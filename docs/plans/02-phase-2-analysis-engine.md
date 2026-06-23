# Phase 2 Plan — Close the Analysis Engine gaps

_Roadmap reference: `docs/trading_roadmap.md` Phase 2 (Week 6–8)._

The core indicators (RSI, EMA, ATR) are working end-to-end. What's missing is **price-structure analysis** (S/R, HH/LL), full coverage across symbol+timeframe combos, and a few UI niceties.

---

## §1 Support / Resistance zone detection

**Why:** The roadmap calls out "Support/Resistance zones" as a core Phase-2 indicator. Strategy quality jumps dramatically when entries / stops are placed relative to real S/R, not just round numbers.

**Approach** — pivot-based detection (simplest that works):
- A **pivot high** is a candle whose `high` is greater than the high of the N candles before and after it.
- A **pivot low** is the mirror.
- A **zone** is a cluster of pivots within `0.5 × ATR` of each other; the cluster's mean price is the zone level, and its strength = number of pivots.

**Steps**
1. New module `services/data/levels.py`:
   ```python
   def find_pivots(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame: ...
   def cluster_pivots(pivots: pd.DataFrame, atr: float) -> list[Level]: ...
   ```
2. Add a `Level` table to Prisma:
   ```prisma
   model Level {
     id        String   @id @default(cuid())
     symbol    String
     timeframe String
     price     Decimal  @db.Decimal(18, 8)
     kind      LevelKind   // SUPPORT | RESISTANCE
     strength  Int          // # of pivot touches
     firstSeen DateTime
     lastSeen  DateTime
     @@index([symbol, timeframe, kind])
   }
   ```
3. Compute and upsert levels after each candle-ingestion cycle (same hook as `calculate_indicators()`).
4. New API route `GET /api/levels?symbol=...&timeframe=...` returning the strongest N zones.
5. Wire it into the dashboard: a "Key Levels" list below the indicator sidebar, sorted by strength.

**Done when:** A 60min XAUUSD query returns 3–10 zones near the current price; the UI shows them as a list.

---

## §2 Higher-High / Lower-Low trend structure

**Why:** Trend structure (`HH-HL` = uptrend; `LH-LL` = downtrend; mixed = range) is the single most important context for any pullback or breakout strategy. Without it, your existing signalGenerator can flag setups that fight the trend.

**Approach** — operate on the pivots from §1:
- Walk the most recent ~10 pivots.
- Classify the structure: `HH+HL → UPTREND`, `LH+LL → DOWNTREND`, otherwise `RANGE`.
- Detect a **break-of-structure (BoS)** when the latest pivot violates the prior swing.

**Steps**
1. Add `classify_structure(pivots) -> StructureSnapshot` in `services/data/levels.py`.
2. Persist the latest snapshot per `(symbol, timeframe)` in a small `Structure` table or as a Redis key (it's small + cheap to recompute).
3. Surface it via `GET /api/structure?symbol=...&timeframe=...` returning `{ trend: "UPTREND" | "DOWNTREND" | "RANGE", lastBoSAt: ISOString, lastSwingHigh, lastSwingLow }`.
4. Use it as a **gate inside `signalGenerator.ts`**: skip LONG signals when trend is `DOWNTREND`, and vice versa. Log the skip reason.
5. Show the trend label as a pill in the Navbar (e.g. `XAUUSD 60min · UPTREND ▲`).

**Done when:** Trend pill appears in the UI and updates when timeframe changes; signal cron logs `status=skipped reason="against_trend"` where applicable.

---

## §3 Backfill indicator coverage

**Why:** DB shows 905 candles but only 740 indicators. Either some combos never had `calculate_indicators()` called, or the leading-NaN skip is dropping legitimate rows. Either way, the UI's IndicatorSidebar shows "—" for some pairs.

**Steps**
1. One-shot script `services/data/backfill_indicators.py` that iterates every `(symbol, timeframe)` present in `Candle` and calls `calculate_indicators()` for each.
2. After running, verify: `SELECT symbol, timeframe, COUNT(*) FROM "Indicator" GROUP BY 1,2` matches the candle distribution (minus the first ~14 rows per pair where indicators need warmup).
3. Permanent fix: ensure `services/data/main.py` calls `calculate_indicators` for **every** TF, not just whichever the ingestion loop touched.

**Done when:** Every symbol+timeframe that has 50+ candles also has indicators within ~20 rows of the candle count.

---

## §4 Display EMA200 in the IndicatorSidebar

**Why:** EMA200 is computed and stored but the sidebar only renders EMA20 + EMA50. EMA200 is the most-watched trend filter in retail trading and it's a five-line change.

**Steps**
1. In [apps/web/app/components/IndicatorSidebar.tsx](apps/web/app/components/IndicatorSidebar.tsx), add an `<IndicatorRow label="EMA 200" ... />` after the EMA50 row.
2. (Optional) Color it conditionally: green if `close > ema200`, red if `close < ema200`, since that's how traders read it at a glance.

**Done when:** EMA200 visible in the sidebar with the same formatting as EMA20/EMA50.

---

## §5 (Stretch) Overlay your computed levels on the chart

**Why:** The TradingView widget shows TV's own feed. Your S/R levels and EMAs aren't drawn on it. That's a UX disconnect.

**Approach**
- TradingView's free embed widget doesn't accept programmatic overlays.
- Two options:
  - **Cheap**: render S/R levels as a list next to the chart with current distance from price.
  - **Real**: replace TV widget with `lightweight-charts` (TV's open-source library), feed it your own candles from `/api/candles`, and draw horizontal lines for each level.

**Recommendation:** Defer to a separate plan after §1–§4 land. The "Key Levels" list from §1 already gives 80% of the value.

---

## What this plan leaves out

- **Liquidity sweep detection** (smart-money / ICT concept). Listed in the roadmap pre-phase but not in Phase 2's "Indicators to implement" table. Defer.
- **Pivot points (daily/weekly classic formula)** — common but not in the roadmap. Skip until a strategy needs it.
- **Order-flow / volume profile** — Phase 4+ territory.

## Estimated effort
- §1 S/R detection (incl. UI list): **3–4 hr**
- §2 HH/LL structure: **2–3 hr**
- §3 indicator backfill: **30 min**
- §4 EMA200 display: **10 min**
- §5 chart overlay (stretch): **half a day or more**

**Total for §1–§4: roughly one day.**
