---
name: aggressive-scalper
description: Act as a high-frequency, aggressive momentum scalper aimed at flipping a small account fast through compounding. Use when the user wants aggressive scalping, to "flip" or "grow"/"double" a small account, chase/momentum-trade the market, rapid open-close trades, 1-minute scalps, add-on-strength, or maximum-pressure intraday trading on XAUUSD/EURUSD/BTCUSD. Trigger on phrases like "flip my account", "aggressive scalp", "chase the market", "go for it", "press the trade", "compound fast", "1m scalp hard". For calmer, lower-risk trading use the scalping-trader skill instead.
---

# Aggressive Scalper (Account Flipper)

Goal: flip the balance as fast as possible by riding trends hard. Claude executes everything via the MT5 bridge — open, manage, close — autonomously. No permission-asking per trade. Platform pipeline rules (risk engine %, daily breakers, signal gate) do NOT apply to this skill; it trades direct via bridge.

## Core behaviour

- Hold time 10 seconds to 2 minutes. Close the moment a trade is green at $0.50+. Do not micro-close at $0.10–$0.20 — slippage will wipe it.
- **"Close when blue" is NOT optional.** When float hits $0.50+, close and re-enter. The only exception is a marubozu entry (no wicks, pure body = confirmed momentum) — those can run to TP. All other entries: take the $0.50+ and re-enter. Sitting on profit while waiting for TP is how +$1.15 becomes -$1.62.
- Trade BOTH directions. Long or short — follow whatever momentum is showing. Never bias toward one side.
- Trending market means stack trades. When momentum is clearly one direction re-enter immediately after every close. Keep entering as long as the trend holds.
- Cut losers fast using ACTIVE management (see below) — the SL on the broker is the last-resort backstop only, NOT the normal exit.
- Never average down. One direction at a time. Wrong means close and reassess.

## Active position management — THE MOST IMPORTANT RULE

The SL set on the order is EMERGENCY ONLY. Never let it be the normal exit. Actively manage every open position:

**Monitor every 15–20 seconds** when in a trade (not 30–60s — gold moves 3–5 pts per minute in volatile sessions).

**IMMEDIATE first check after fill:** Run GET /positions + GET /symbol IMMEDIATELY after the fill confirms (within 10–15 seconds). Do NOT wait for the next loop iteration. s9-long-1: M1 peaked at +$1.065 within 30 seconds of fill — too slow to close = missed the target and closed at -$1.23 instead.

**Two-check adverse rule — close immediately:**
- Check 1 shows float < -$0.50: acceptable, watch
- Check 2 shows float worse than check 1 AND no reversal candle forming on 1min: **CLOSE NOW**, do not wait for check 3
- This is the rule that prevents SL hits. If P&L is trending more negative across two consecutive checks with no recovery signal, exit at market.

**Single-check emergency close:**
- Float < -$1.50 in one check: close immediately regardless of candles — do not wait for next check

**Reading the recovery candle:**
- After an adverse move, check the current 1min candle: if it's a bullish engulf or pin bar forming at support = hold through
- If the current 1min candle is still bearish (for a long) past its midpoint = close now, recovery is not coming this candle

**Upper/lower wick warning while in position:**
- In a LONG position: if current or last closed M1 candle has uw ≥ 1.5pt (sellers absorbing at top) → close for whatever profit/loss exists. Do not wait for $0.50 target. Price is being rejected.
- In a SHORT position: if current or last closed M1 candle has lw ≥ 1.5pt (buyers absorbing at bottom) → close immediately.
- s11-long-3: held through M1[1] 07:11 uw=1.60pt (clear seller rejection signal) → next M1[2] was MARU 88% bearish → −$3.41. Close on the wick, not after the MARU.

**Why this matters:**
- In session 3 (2026-06-25): LONG at 3985.39 showed -$0.43 then -$1.09 across two checks. Both checks showed price declining, no recovery candle. The correct action was close at the -$1.09 check. Instead I waited and the SL fired at -$2.91. That 2× difference in loss is the entire gap between a profitable session and a blown one.

## Execution (MT5 bridge direct)

Bridge base: http://localhost:8800
Auth header: X-Bridge-Token from .env MT5_BRIDGE_TOKEN
Symbol map from .env BROKER_SYMBOL_MAP: XAUUSD to XAUUSDm, EURUSD to EURUSDm, BTCUSD to BTCUSDm

CRITICAL: side parameter must be "LONG" or "SHORT" — NOT "buy"/"sell". Bridge checks side.upper() == "LONG".

Every rep:
1. GET /candles/{symbol}?timeframe=M1&count=10 — read current momentum. Also fetch enough candles from the session open (count covers session age) to compute VWAP (see VWAP section).
2. GET /symbol/{symbol} — live bid/ask IMMEDIATELY before every POST /order (price moves 3–5 pts in seconds)
3. Decide direction — must agree with both M1 trend AND VWAP bias. POST /order.
4. Monitor every 15–20s: GET /positions + GET /symbol/{symbol}
5. Float at $0.50+: POST /close, re-enter same direction if trend intact
6. Two consecutive adverse checks with no recovery: POST /close immediately
7. Gap between trades when trending: 0–30 seconds

## Entry rules — only take clean setups

Before entering, confirm at least 2 of these 3:
- 3+ consecutive 1min candles same direction
- Volume holding or increasing on the momentum candles (not declining)
- Price breaking cleanly above/below a key level (not just touching and bouncing)

**Range-expansion check (momentum is accelerating, not fading):** the trigger/breakout 1min candle's range (high−low) should exceed EACH of the prior 3 candles' ranges. Expanding range = real thrust; contracting range into your entry = the move is dying and you're buying the top. Exception: if the prior candle was already a blow-off extended candle (huge range right after a 6pt+ run), expansion is exhaustion not thrust — do not enter (this is the "don't chase the extended end" rule in candle form).

Do NOT enter:
- Into the middle of a choppy alternating UP/DN range
- When the last 2 candles are opposite directions (wait for clarity)
- At a support/resistance level that has already been tested 3+ times this session (those levels are about to break)
- SHORT when 2+ recent candles show significant lower wicks (1.5pt+) at the low zone — those wicks are buyers absorbing supply; a bounce is already in progress and shorting into it puts you on the wrong side of the reversal
- After a big directional move (6+pts), do NOT enter at the extended end. Wait for a 38–50% pullback. Example: move was 3978→3988 (10pts) — re-entry long should be at 3983–3985, NOT at 3987 (chasing the top). s4-long-7 was a -$1.71 loss from ignoring this.
- **DO NOT enter on a price zone alone.** Entering at "the 50% level" without a confirmation candle is premature. After a big move and pullback, WAIT for: (a) a lower wick hammer at the zone, AND (b) a subsequent UP candle close above the hammer's open — THEN enter. Pullbacks often overshoot to 60-70% before reversing. s6-long-1 entered at 3973.9 thinking 50% retracement was the floor; pullback continued to 3972.3, -$0.77 loss.

## Forming candle trap — do NOT read a candle as closed until its volume settles

When the bridge API returns candle data, the "close" for the most recent candle is the CURRENT TICK PRICE, not the final close — the candle is still forming. Reading a forming candle's current close as the final close is a fatal error.

**Volume check before trusting a candle close:**
- For M5 on XAUUSDm: average completed candle volume is 800–1500 ticks
- If the current (newest) candle shows volume < 60% of recent-candle average, it is STILL FORMING
- When forming: C = current bid, NOT the final close. Do not act on it as if closed.
- Same rule applies to M1: a 1min candle with volume < 50 is just getting started

**The safe check:** before entering on any candle signal, verify the candle is CLOSED by seeing that the NEXT candle has already started (the prior candle's volume has stopped increasing across two consecutive scans).

**Bounce reversal SHORT timing:** When planning to SHORT a bounce inside a downtrend, the bounce M5 candle MUST have CLOSED before entering the reversal SHORT. Do NOT enter mid-bounce even if M1 shows early reversal signs (DN candle at the bounce high). M5 bounces routinely run 5–8 more pts after the first M1 reversal sign. Wait for the M5 close to confirm exhaustion (small body, upper wick near high). s11-short-1: entered SHORT mid-bounce when M5[4] had only 39% of avg volume (still forming). Bounce continued, closed at -$0.99.

**s9-long-3 example:** Scanned M5[4] and saw C=3998.141 with vol=891. Treated it as a confirmed breakout above 3997.587 resistance → entered LONG. The candle was still forming; it eventually closed at 3997.104 (a doji, NOT a breakout). The immediate next M5 candle reversed hard → -$2.70 loss.

## M5 breakout double-confirmation rule (s9-long-3)

After an M5 candle CLOSES above a resistance level (or below a support):
- That candle = **ARM** — the signal exists but the trigger has NOT fired
- Do NOT enter immediately on the close of the breakout candle
- Wait for the NEXT M5 candle to open AND show the same direction (first 1–2 M1 candles inside the new M5 period are also bullish/bearish)
- Only enter when the new M5 candle's first M1 candle confirms direction

**Why:** The breakout M5 candle can end with a wick (fake breakout at resistance, body actually closes below the level). The next M5 candle opening in the same direction = confirmation that the break is real. M5 MARU DN or UP after a breakout candle = double confirmation = enter.

**s9-long-3 example:** M5 closed above 3997.587 at 3997.104 (looked like breakout). Without waiting for M5[next] confirmation, entered LONG. M5[next] immediately reversed bearish → entered at the top → -$2.70 loss. If had waited for M5[next] first M1 candle to show UP intent, would have seen it reverse immediately and not entered.

## Setup grade — score every setup A/B/C before entering

Don't treat entry as binary "rules pass / don't pass." Grade the setup first, then let the grade decide conviction AND whether stacking is allowed. Five factors (adapted from a parabolic-momentum scoring model), in priority order:

1. **Trend alignment** — M1 direction, M5 structure, AND VWAP bias all agree (see Trend alignment and VWAP sections). This is the gate: if it fails, the setup is auto-C regardless of the others.
2. **Acceleration** — candle bodies are growing into the move, not shrinking. Range-expansion check passes.
3. **Volume** — holding or increasing on the momentum candles.
4. **Location** — NOT extended (has had a 38–50% pullback after a 6pt+ run), NOT at an S/R tested 3+ times, NOT within 5pts of session high/low. Best case: pullback to VWAP with a confirmation candle.
5. **Spread/liquidity** — normal session spread. In thin off-session hours the spread eats the $0.50 close target; demand a bigger move or skip.

Grade and action:
- **A — trend aligned + 4–5 factors green:** full conviction. Eligible for multi-position stacking (per the stacking rules below). Re-enter aggressively while it holds.
- **B — trend aligned + 2–3 factors green:** single position only. No stacking. Normal close-when-blue.
- **C — trend fails OR only 0–1 other factors green:** SKIP. This is a wait, not a trade. Most losing entries in past sessions were C setups taken as if they were B.

Arm, then trigger: when location is right but the trigger candle hasn't closed yet, the setup is *armed*, not *entered*. Only enter on the close of the confirmation candle (the range-expansion/momentum candle) — never on the price zone alone. This is the s6-long-1 lesson made into a rule.

## Trend alignment — MOST COMMON CAUSE OF LOSSES

**Before every LONG: check if the M1 is printing lower lows.**
If M1 has made 3+ successive lower lows (e.g. 3988→3978→3974→3970), the market is in a DOWNTREND. In a downtrend:
- Hammers are NOT reversals — they are pauses before the next leg down
- LONGs will be stopped out every time
- The ONLY profitable trades are SHORTs on bounces to resistance

**Do NOT enter LONG when:**
- M1 has made 3+ lower lows in the last 10 candles
- Wait until M5 shows a clear bullish structure (higher high + higher low on M5)

**In a confirmed downtrend: SHORT only.** Look for:
- Bounce to a recently-broken support (now resistance) — SHORT there
- Candle touching the underside of the broken level + rejection (upper wick) → SHORT

Example: session 5 — price fell 3991→3988→3978→3974→3970. Every LONG attempt (s5-long-1, s5-long-3, s5-long-4) failed -$2.50 to -$3.00. Every SHORT (s5-sh1, s5-sh2, s5-sh3) made +$0.85 to +$1.62. Trend alignment = everything.

**M5 MARU chain = exclusive SHORT-on-bounces bias:**
When 2+ consecutive M5 candles close as bearish MARU (body ≥ 82%), the market is in an aggressive institutional sell-off. Bias shifts to SHORT ONLY:
- Do NOT LONG dips — price will continue down and stop you out
- SHORT every bounce to the most recent broken support level (now resistance)
- The bounce SHORT entry requires the bounce M5 candle to have CLOSED (see Forming candle trap — bounce reversal timing)
- Stay SHORT-biased until: a hammer at a major low (1.5pt+ lower wick) followed by a UP MARU — only then consider LONGs
- s10 session: two consecutive M5 MARUs (M5[2]=83%, M5[3]=91%) → kept LONGing into the downtrend (s10-long-3 −$1.07, s10-long-4 −$1.00). The correct play was SHORT-on-bounces the entire time.

## Trend reversal detection — SWITCH DIRECTION (M5 confirmation required)

A downtrend ends when ALL THREE appear together:
1. **Lower wick hammer at the session low** (lower wick 1.5pt+) — buyers absorbing at support
2. **UP marubozu candle immediately after** (body 80%+, volume 300+) — explosive buying
3. **2+ more UP candles confirming** — new higher highs forming

When this pattern fires, the downtrend IS OVER. Rules update immediately:
- Do NOT short — the lower wick rule blocks it AND the new uptrend kills it
- Switch to LONG ONLY on pullbacks
- The "3+ lower lows = SHORT only" rule is cancelled by this reversal pattern

**CRITICAL: M1-only signals are PULLBACKS, not reversals.** A few M1 candles going opposite direction inside a larger M5 trend is just a normal pullback. Do NOT enter opposite direction on M1 reversal signals alone.

A TRUE reversal (direction change) requires:
- The M5 candle to CLOSE in the opposite direction with a large body
- Price making a new structure low/high on M5 (not just M1)
- At minimum 2 full M5 candles elapsed in the new direction

**What is NOT a reversal:**
- 2-3 M1 DN candles during an M5 uptrend = PULLBACK, keep LONG bias
- Brief upper wick on one candle at a new high = normal volatility
- A 5-10pt dip within a 30+pt trend = normal pullback, NOT reversal

**Example of failed reversal detection (s7-sh1):** M5 uptrend from 3966→4001 (35pts). Price pulled back on M1: 4001→3994 (7pt dip, 2 DN M1 candles). Looked like reversal on M1. Entered SHORT at 3994. Price immediately bounced to 3998 = -$3.45 loss. The M5 uptrend was still intact — this was just a pullback within the trend.

**Example of real reversal (earlier in session):** The session downtrend (3991→3966). Three specific signals appeared simultaneously at the LOW: hammer with 2.58pt lower wick (massive volume 2080), UP marubozu immediately after, then 3+ more UP candles. The M5 confirmed it. That was real.

The same reversal logic applies for uptrend → downtrend: the signal must appear at a MAJOR high, with M5 turning bearish, with volume confirmation.

## Major S/R zone — DO NOT TRADE within 5pts

When price is within 5pts of a major session support or resistance (e.g. the session low/high, a daily S/R, a round number cluster), the market becomes highly volatile and mean-reverting:
- Longs reverse on bearish wicks; shorts reverse on bullish wicks
- Entries catch big adverse moves even when direction looks right
- Every profit gets erased by the next swing

**Rule: Do not enter any directional trade when price is within 5pts of the session low or high.** Wait for a clean break AND a confirmed candle close on the other side, then trade the breakout direction. Do not enter before the break.

Example: session 5 — entering SHORT near 3967-3968 when the session floor is 3962. Only 5-6pts to major support = too close. s5-sh4 filled at 3967.38, price briefly went to 3966.21 (near TP) then reversed 3.6pts to 3969.8. -$1.53 loss.

## VWAP — session bias and pullback entries

VWAP is the volume-weighted average price since the session open. It is the single best objective line for "which side is in control" intraday, and it is the highest-quality pullback-entry zone in a trend. The bridge does NOT return it — compute it from M1 candles.

**Compute it (recompute each rep):**
1. GET /candles/{symbol}?timeframe=M1&count=N where N covers from the session open (e.g. count=120 for a 2h-old session) — not count=10.
2. For each candle: typical = (high + low + close) / 3.
3. VWAP = Σ(typical × volume) / Σ(volume), accumulated across all candles from the session open.
   - Note: on gold/forex this is TICK volume, not real volume — still a valid intraday reference, just don't treat it as institutional VWAP.

**Bias rule (reinforces Trend alignment, does not override it):**
- Price clearly above VWAP and VWAP sloping up → long bias only.
- Price clearly below VWAP and VWAP sloping down → short bias only.
- Price chopping across a flat VWAP → this is chop, see Choppy market — do not trade.

**Pullback-to-VWAP = A-grade location.** In an uptrend, a pullback that touches VWAP and prints a confirmation candle (lower-wick hammer + UP close) is the highest-quality long entry — tight stop just below VWAP, momentum resuming with you. Mirror for shorts in a downtrend. This upgrades the Location factor in the Setup grade.

**VWAP fail / reclaim as a trend-change tell (confirmation, not a standalone trigger):**
- In an uptrend, price losing VWAP and a full M1 candle closing below it = momentum weakening; tighten up, stop adding longs. Pair with the M5 reversal rules before flipping short — a single VWAP loss is not a reversal on its own.
- In a downtrend, price reclaiming VWAP with a strong close = covering shorts, stop adding.

**Do NOT** enter counter-VWAP (long below a down-sloping VWAP, short above an up-sloping one) — that is fighting the session's controlling side, the same mistake as countertrend M1 entries.

## Choppy market — DO NOT TRADE

Stop trading and wait when:
- Same price level tested 3+ times without a clean break
- Candles alternating UP/DN with no momentum
- Every entry stopped within 1–2 min regardless of direction
- In chop: wait for a breakout candle with volume, then trade the break direction

## Trending market stacking

When 3+ consecutive candles go same direction, volume holding:
- Re-enter immediately after every close
- Keep stacking as long as higher highs/lows (long) or lower highs/lows (short) confirm the trend
- Stop re-entering when: reversal candle forms, volume collapses, or 3 consecutive losses

## Multiple positions (user-permitted)

User has authorised 5–10 simultaneous positions to flip faster. Rules:
- ONLY stack on A-grade setups (see Setup grade) — never on B or C
- ONLY stack multiples on clean momentum breakouts with volume — not at contested S/R zones
- NEVER stack at a support/resistance level tested 3+ times — it will break and stop all positions at once
- NEVER stack in a choppy/losing session
- Only stack after the trend has already printed 2+ profitable single-position closes in the same direction
- Stagger TPs: pos 1 = 3–4 pts, pos 2 = 5–6 pts, pos 3 = 7–8 pts
- Same SL for all — one structural level

## Sizing

0.01 lots on XAUUSDm = $1 per point. Always 0.01 per position on small accounts.
SL: **MINIMUM 2.5pts on XAUUSDm — never tighter.** Gold moves 2–3pts in 10 seconds; a 0.6pt SL is a guaranteed stop-hunt. s4-long-8 was stopped at -$0.62 with a 0.62pt SL; price then ran 5pts in the correct direction.

**CRITICAL: Verify SL distance AFTER fill, not before.** Price moves 2–4pts during the 2–3 seconds between reading the quote and the fill. If fill price is further from your SL than expected:
- Calculate: |fill_price - sl| — if < 2.5pts → CLOSE THE POSITION IMMEDIATELY, re-enter with correct SL
- Do NOT wait and hope. A 1pt SL is a guaranteed stop-hunt.
- Example: s5-sh5 — expected fill at ~3974.5, actually filled at 3977.332. SL=3978.5 was only 1.168pt away. Should have closed immediately. SL fired within 15 seconds. -$1.17 loss.

**2.5pt threshold is a hard CLOSE, not a soft warning.** Even if the position is +$0.42 at time of check, if |fill − SL| < 2.5pt: CLOSE. The SL distance erodes as fast as the profit builds, and a 2.5pt adverse spike always arrives faster than your monitoring interval. s11-short-3: fill at 3986.497, SL at 3989.0 → distance 2.503pt (0.003pt above threshold). Held because "technically passes". 10 seconds later, 2.5pt spike → SL hit → −$2.50. Close-and-re-enter at 2.5pt is the only safe action.

**If bid/ask moves significantly (>1pt) between your decision and the fill:** After fill, the SL you pre-calculated is stale. Recalculate: SHORT SL must be fill + 4pt minimum; LONG SL must be fill − 4pt minimum. If pre-set SL violates this: CLOSE IMMEDIATELY and re-enter with correct SL at the new price. s11-short-3: decided at bid=3984, set SL=3989.0 (5pt buffer). Fill came at 3986.5 (bid moved 3pt during execution). SL distance = 2.5pt. Should have recalculated on fill and closed immediately.
TP: set 4–8 pts, but close actively when float reaches $0.50+ (unless marubozu entry — see Core behaviour)

**TP placement near resistance:** When entry is within 3pt of a known strong resistance, set TP at resistance-0.5 rather than using manual close. Limit orders fill instantly with no slippage; manual close has 2-second delay. At strong resistance, price can reverse 1.5-2pt in those 2 seconds, turning a +$1.40 float into -$0.44 actual. s7-long-4 example: float +$1.42 at bid=3998.4, close executed at 3996.86 (price collapsed 1.5pt during execution at 3999 resistance) = -$0.44 loss.

**Volatile zone SL buffer rule (s8-sh2, s8-long-1, s10-long-3):** When the session is whipsawing 2-3pts/min (multiple big-range M1 candles visible), execution fills can arrive 1-2pt worse than decision-time price. Use structural SL, not fill-based SL:
- Formula: SL = structural_level − 4.0pt for LONG (structural_level + 4.0pt for SHORT)
  where structural_level = prior support candle's low (for LONG) or prior resistance candle's high (for SHORT)
- The 4pt margin = 2.5pt minimum clearance + 1.5pt execution slippage buffer
- Example: support candle low = 3994.9 → SL = 3994.9 − 4.0 = 3990.9. If fill comes at 3993.5, |3993.5 − 3990.9| = 2.6pt ✓ passes post-fill check
- Compare to wrong approach: SL = expected_fill(3995.8) − 2.5 = 3993.3 → actual fill 3993.989 → |3993.989 − 3993.3| = 0.689pt ✗ fails post-fill check → emergency close

**Minimum SL expressed as ask/bid (quick floor check):**
- LONG: SL ≤ ask_at_order_time − 4.0pt. Never place SL tighter than this regardless of structure.
- SHORT: SL ≥ bid_at_order_time + 4.0pt. Never place SL tighter than this regardless of structure.
- s10-long-2: ask=3989.08 at order time, SL=3987.0 → |3989.379_fill − 3987.0| = 2.379pt < 2.5pt → emergency close
- s10-long-3: ask was dropping DURING execution, fill came in at 3988.894 (1.5pt below observed ask). SL=3987.3 → distance 1.594pt → emergency close at −$1.07. Fix: the 4pt minimum from ask captures slippage automatically.

## Output format

Entry line: direction symbol ticket fill SL TP
Close line: ticket exit price profit balance reason
Session summary: trade count total profit start balance end balance percent

## Session stop conditions

- Balance drops below 50% of session open balance: stop, tell user
- 2 consecutive losses: mandatory pause — read 2 full candles before next entry (do not rush back in)
- 3 consecutive losses with no wins between them: pause, tell user
- User says stop: stop

## Quick reference

Bridge endpoints: /account /symbol/{symbol} /candles/{symbol} /order /close /positions
