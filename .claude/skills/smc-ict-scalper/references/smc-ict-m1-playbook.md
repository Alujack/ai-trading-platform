# SMC / ICT on the 1-Minute Chart — Detector Playbook

Mechanical definitions for every term the skill uses. These are compressed from `docs/research/ict-concepts.md` §3 (the codeable spec) and from the shipped detectors in `services/data/strategies/ict/` — same geometry, retuned for M1 execution on XAUUSDc.

Read this when you need to decide *"is that actually a sweep?"* mid-session. Everything below is defined on **closed** candles only.

---

## 0. Notation

- `ATR` = ATR(14) on the **M1** series unless stated. On XAUUSDc in an active killzone this is typically **1.5–2.5 points** (measured 2.31 with gold at 4422 — re-measure each session rather than trusting this range); in the Asian session it collapses under 0.5 and every threshold below becomes meaningless — that is one more reason the killzone gate exists.
- `body[i] = |close − open|`, `range[i] = high − low`.
- 1 point on XAUUSDc = 1.00 in price = **1 USC ($0.01) per 0.01 lot** — this is a CENT account, see runbook §3.
- "pool" = a cluster of resting stops: equal highs/lows, a swing extreme, session high/low, PDH/PDL, or a round number.

---

## 1. Swings and structure

```
swing_high(i, k): high[i] == max(high[i-k : i+k+1])     # k = 2 on M1
swing_low(i, k):  low[i]  == min(low[i-k : i+k+1])
```

Matches `P.find_swings(bars, k=2)`. **A swing is only confirmed `k` bars after it prints** — on M1 that is a 2-minute lag. Do not treat the current bar's high as a swing high; you cannot know that yet. This is the look-ahead trap that `docs/research/ict-concepts.md` §3.11 calls the #1 killer, and it is just as fatal live as in a backtest.

- **BOS** (break of structure): close beyond the last confirmed swing in the *trend* direction → continuation.
- **CHoCH**: the first break *against* the prevailing structure → potential reversal.
- **MSS**: a CHoCH whose breaking candle qualifies as displacement (§3). This is the only structure event this skill trades.

Track structure state as `{bullish, bearish, neutral}` on M5, updated on confirmed breaks. M1 structure is for triggers and cuts, never for bias.

---

## 2. Liquidity — where the stops are

### Building the pool map (do this once per killzone, refresh every ~10 min)

| Source | How to get it | Weight |
|---|---|---|
| PDH / PDL | `GET /candles/{s}?timeframe=daily&count=3`, prior bar's high/low | Highest |
| Session high/low | `timeframe=60min&count=12`, extremes since session open | High |
| M15 equal highs/lows | swing highs (or lows) within `0.25 × ATR(M15)` of each other, 2+ pivots | High |
| M1 micro equal highs/lows | same test on the last 10–20 M1 bars, tolerance `0.25 × ATR(M1)` | **The M1 entry pool** |
| Round numbers | x.00 / x.50 clusters on gold | Medium, confirming only |

**BSL** (buy-side liquidity) rests *above* highs. **SSL** rests *below* lows. Longs raid SSL first; shorts raid BSL first — always the opposite side of your intended direction. If you find yourself planning a long off a BSL sweep, you have the model backwards.

### Sweep detection (the manipulation)

```
sweep_BSL at i:  high[i] > pool_high  AND  close[i] < pool_high
sweep_SSL at i:  low[i]  < pool_low   AND  close[i] > pool_low
```

Mirrors `P.detect_sweep(..., lookback=5)`. Quality grading:

- **Clean** — wick pierces by ≥ `0.3 × ATR`, body closes fully back inside, wick is ≥ 50% of the candle's range. This is the one you trade.
- **Marginal** — pierces by a tick, or the body closes right at the level. Wait for a second attempt or skip.
- **Not a sweep** — the candle *closes* beyond the pool. That is a **break**, i.e. continuation. Trading it as a reversal is the most expensive mistake available in this model.

**M1-specific:** sweep the micro pool. A session-high sweep leaves your stop 8–12pt behind and no realistic M1 target clears 2R from there. Reserve those for M5 setups and a wider frame.

---

## 3. Displacement — the institutional-participation gate

```
is_displacement(i):  body[i] >= 1.5 * ATR  AND  body[i] / range[i] >= 0.5
```

Matches `P.is_displacement(...)` (spec §3.4, D≈1.5–2.0). Displacement is what upgrades a CHoCH to an MSS and what *creates* the FVG you enter from. No displacement → no trade, however pretty the sweep was.

On M1 gold in a killzone that threshold is roughly a **2.2–3.8 point body** at current volatility (1.5 × a ~2.3 ATR). Recompute it from the live ATR every session — it is a multiple, not a constant. A 0.4pt "push" is noise. If you have to squint to call it displacement, it isn't.

**The MSS test, in full:** within 1–3 bars of the sweep, a displacement candle **closes beyond the last opposing confirmed M1 swing**. Both halves are required — a big candle that does not break structure is a wick-fill, and a structure break on a doji is nothing at all.

---

## 4. Fair Value Gap (FVG)

Three-candle imbalance, defined on the middle candle `i`:

```
bullish FVG:  low[i+1] > high[i-1]     gap = (high[i-1], low[i+1])
bearish FVG:  high[i+1] < low[i-1]     gap = (high[i+1], low[i-1])
CE (consequent encroachment) = midpoint of the gap    # the reaction level
```

Matches `P.find_fvgs(bars, require_displacement=True)` and `FVG.ce`. Rules:

- **Only trade FVGs born from a displacement candle.** An imbalance left by a limp candle is a gap, not an institutional footprint.
- **Fill state:** unfilled → partially filled (price enters) → **mitigated** (price fully traverses). A mitigated FVG is dead — remove it from the map. `P.fvg_unmitigated_until` is the in-repo version of this check.
- **Entry level:** CE is the default. The far edge is a better price and a worse fill rate; on M1 gold the gaps are 0.5–2.0pt wide, so the difference is under a point — take CE and get filled.
- An unfilled M15/H1 FVG on the way to your draw is a **warning**: price often reacts there. Do not set a TP beyond an untapped opposing FVG without expecting a stall at it.

---

## 5. Order Blocks and their relatives

- **Bullish OB** — the last **down-close** candle immediately before a displacement leg up. **Bearish OB** — the last up-close candle before a leg down. Zone = `[low, high]` of that candle; `proximal` = the edge price meets first, `distal` = the far edge. See `P.find_order_blocks` / `OrderBlock.proximal`.
- **Validity:** the following move must break structure and/or leave an FVG. An OB with neither is a random candle you have labelled.
- **Invalidation:** a candle **closes** beyond the distal edge. On M1, a wick through the distal edge is normal — the close is what kills it.
- **Breaker** — an OB that got violated, then price returns to it: polarity flips (bullish OB → resistance). Breakers are strong on M1 because the trapped side is fresh.
- **Mitigation block** — an OB price retested without closing past; trade continuation in the OB's original direction.

Best M1 entries stack **OB proximal ∩ FVG CE**. That overlap is usually a 0.5–1.5pt window on gold — tight enough for a real stop.

---

## 6. Premium / discount and OTE

Take the swing pair that defines the current leg (`A` = low, `B` = high):

- `equilibrium = (A + B) / 2`. Above it = **premium** (favor selling), below = **discount** (favor buying).
- **OTE** = the 0.62–0.79 retrace of the displacement leg, sweet spot **0.705**.

The rule this actually enforces: **do not buy in premium, do not sell in discount.** A long entry above equilibrium on its own leg means you are buying what smart money is distributing. When OTE overlaps the FVG and the OB, that is the maximum-confluence entry the model can produce — and on M1 it is rare enough that seeing it should raise your size decision to the two-ticket scale-out, not lower your standards elsewhere.

---

## 7. Time — killzones, judas, Power of Three

Windows in **New York local time**, matching `services/data/strategies/ict/killzones.py`:

| Killzone | NY time | Character |
|---|---|---|
| London | 02:00–05:00 | Sweeps the Asian range. Cleanest sweep→MSS setups of the day on gold. |
| NY AM | 07:00–10:00 | Continues or reverses London. Highest volatility; widest stops needed. |
| Silver Bullet | 10:00–11:00 | Retrace into the morning FVG, continuation in the session direction. Narrow, mechanical, high quality. |
| Asian | 20:00–00:00 | **Do not trade.** Builds the range you will raid later. Spreads on XAUUSDc blow out and ATR collapses. |

DST is handled by converting through `zoneinfo`, never a fixed offset (spec §3.8). The command is in the runbook.

**Judas swing** — the false move right after a session open, engineered to trap. On M1 this looks exactly like a breakout with volume. Rule of thumb: in the **first 15 minutes** of a killzone, a move against the H1 bias is a judas until proven otherwise. Do not chase it; wait for it to sweep the pool and reverse, then trade the reversal. That reversal *is* the killzone's real setup, and it is the single most reliable thing in this playbook.

**Power of Three (AMD)** — Accumulation → Manipulation → Distribution. Positionally: the Asian range is accumulation, the killzone's first sweep is manipulation, the leg to the opposing pool is distribution. You are trying to enter at the *end of manipulation*. If you find yourself entering after a 15pt run, you are entering during distribution — late, chasing, and about to be the exit liquidity.

---

## 8. Draw on liquidity — how to pick the TP

The draw is the pool price is being *delivered to*, and it is chosen top-down:

1. H1 bias bullish → the draw is the nearest untapped **BSL above** (PDH, session high, M15 equal highs). Bearish → nearest untapped **SSL below**.
2. Prefer a pool that has **never been tapped** this session. A pool already raided today has spent its stops.
3. If an opposing unmitigated M15 FVG sits between entry and the draw, expect a stall there — either take that as the near-target for ticket 1, or skip if it kills the RR.
4. Measure `RR = |draw − entry| / |entry − stop|` **before sending**. Below 1.5 → **no trade**.
   Note this is *stricter* than the in-pipeline `resolve_target` in `_base.py`, which falls back to a synthetic `entry ± min_rr × risk` projection when the liquidity pool does not clear min-RR. That fallback is exactly the "TP with no pool behind it" this skill forbids — here you skip instead.

Never set a TP at "entry + 8 points". A target with no pool behind it has nothing to pull price into it.

---

## 9. SMT divergence (confirmation only)

Two correlated instruments disagree at a pivot: XAUUSD makes a lower low while EURUSD/DXY-proxy does not confirm → bullish SMT. Fetch both via `/candles` and compare the pivots at the same timestamps.

Use it as a **confidence bump on an existing setup**, never as a trigger — same as `P` treats it in the spec (§3.9). On M1 the noise is high enough that SMT alone is close to worthless; at a session extreme with a sweep already printed, it is a genuine tiebreaker between B and A grade.

---

## 10. The forming-candle trap — worked failure

`GET /candles` returns the in-progress bar as the last element. Its `close` is **the current tick**, and its `volume` is partial. Every detector above breaks on it:

- A forming bar shows a big wick → you call a sweep → it closes as a full break the other way.
- A forming bar shows a 2pt body → you call displacement → it closes as a doji.

**Guards:**
1. **Volume floor.** An M1 gold bar with tick volume < 50 is barely started. Compare against the mean volume of the last 10 closed bars — under ~50% of that mean means still forming.
2. **Two-scan confirmation.** Across two consecutive polls, a closed bar's volume stops increasing and a new bar appears after it. Only then is it closed.
3. **Structural habit:** evaluate on `candles[:-1]` and treat `candles[-1]` purely as the live price. This costs you up to 60 seconds of edge and saves you from acting on candles that never existed.

The same failure at M5 scale is documented in the `aggressive-scalper` skill (a "breakout" at 3998.141 that closed as a doji at 3997.104, then reversed for −$2.70). The M1 version of that mistake happens ten times as often.

---

## 11. Setup-quality cheat sheet

| Signal | A-grade | Skip |
|---|---|---|
| Sweep | Clean wick ≥0.3 ATR through an obvious pool, body closes back inside | Candle closes beyond the pool (that's a break) |
| Displacement | Body ≥1.5 ATR, body/range ≥0.5, breaks opposing swing | Body <1 ATR, or big body that breaks nothing |
| Array | FVG CE ∩ OB proximal, unmitigated, in OTE | Mitigated FVG, or "the 50% level" with no candle |
| Location | Discount for longs / premium for shorts | Entering after a 15pt run — that's distribution |
| Draw | Untapped pool ≥2R away | Already-raided pool, or nothing named |
| Time | Inside a killzone, past the first 15 min or trading the judas reversal | Asian session, or 3 min before the window closes |

---

## Sources

- In-repo research and codeable spec: `docs/research/ict-concepts.md`, `docs/research/ict-signal-engine-build-plan.md`
- Shipped detectors: `services/data/strategies/ict/{primitives,sweep_mss,fvg,order_block,killzones,confluence}.py`
- [Key ICT Concepts — TradeZella](https://www.tradezella.com/learning-items/key-ict-concepts)
- [ICT Optimal Trade Entry (OTE) — ictkillzone.com](https://www.ictkillzone.com/ict-ote)
- [Fair Value Gap Trading Strategy — TrendSpider](https://trendspider.com/learning-center/fair-value-gap-trading-strategy/)
- [ICT Power of 3 (AMD) — innercircletrader.net](https://innercircletrader.net/tutorials/ict-power-of-3/)
