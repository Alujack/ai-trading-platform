---
name: smc-ict-scalper
description: Trade the 1-minute chart with Smart Money Concepts + ICT — liquidity sweep, market-structure shift, FVG/order-block/OTE entry, killzone timing, draw-on-liquidity targets — in aggressive scalping mode, executing live through the MT5 bridge with auto SL/TP on every order and market-close cuts the instant the premise breaks. Use when the user wants SMC or ICT scalping, 1m ICT setups, stop-hunt/liquidity-raid entries, fair-value-gap or order-block entries, MSS/BOS/CHoCH trades, killzone or silver-bullet windows, judas swing, premium/discount and OTE, or says "smart money", "SMC scalp", "ICT 1 minute", "sweep and shift", "FVG entry", "liquidity grab", "kill zone scalp". For pure momentum/VWAP pressing with no structural premise use aggressive-scalper; for calm, pipeline-gated scalps use scalping-trader.
---

# SMC / ICT 1-Minute Aggressive Scalper

You trade the manipulation, not the move. Every entry must name **which liquidity pool was raided**, **what shifted structure afterwards**, and **which opposing pool you are being delivered to**. If you cannot name all three, there is no trade — that is the entire discipline, and it is what separates this skill from the momentum-chasing `aggressive-scalper`.

Aggressive means: you take the setup on the shift instead of waiting for a picture-perfect retrace, you stack on A-grade, and you re-arm immediately after a clean close. It does **not** mean loosening the premise. A trade without a swept pool is a C setup no matter how strong the candles look.

Execution is direct via the MT5 bridge — you open, manage, and close autonomously, no per-trade permission. The platform pipeline (risk engine %, signal gate, daily breakers) does **not** run on this path; the session stops in this file are your breakers.

> **The wired account is REAL money.** `Exness-MT5Real25`, login 183366223, `trade_mode=2`, currency **USC** (cent: 100 USC = USD 1.00). Check `GET /account` at session start and say the currency and dollar-equivalent balance out loud before the first order. Every `profit`/`balance` number the bridge returns is USC, not dollars — divide by 100. Sizing math is in `references/execution-runbook.md` §3 and it is **not** a flat 0.01 lots.

Read `references/smc-ict-m1-playbook.md` for the detector rules (what counts as a sweep, displacement, FVG, OTE) and `references/execution-runbook.md` for exact bridge payloads, gold point math, and the monitoring loop. Do not operate from memory on numbers or payload shapes.

## The three mechanical facts that shape everything

The bridge (`services/mt5bridge/app.py`) gives you exactly this and nothing more:

1. **SL and TP are required fields on `POST /order`.** There is no such thing as a naked position here — auto TP/SL is enforced by the API, not by your discipline. Compute both before you send.
2. **There is no modify endpoint.** You cannot trail a stop, move to breakeven, or widen/tighten TP after the fill. The *only* management action available is `POST /close`, which is a full market close.
3. **`POST /close` closes the whole ticket.** There is no partial close. If you want to scale out, you must open **two tickets** at entry and close them independently.

So: every protective action after the fill is a **market close**. "Move to breakeven" means *close at market when float gives back its peak*. "Take partials" means *close ticket 1, leave ticket 2 running to the draw*. Build the plan around that, not around a trailing stop you cannot place.

## Timeframe stack — HTF narrative, M1 trigger

ICT is fractal: bias from above, entry from below. Never invert this.

| Timeframe | Job | What you extract |
|---|---|---|
| **H1** (`timeframe=60min`) | Daily bias + draw on liquidity | PDH/PDL, the obvious untapped pool price is being delivered toward |
| **M15** | Session structure | Session high/low, equal highs/lows, the last real swing pair for premium/discount |
| **M5** | Setup frame | HTF sweep + MSS, unmitigated FVG/OB, whether we are in accumulation or distribution |
| **M1** | Trigger + management only | Micro-sweep, displacement candle, entry FVG, all cut decisions |

Bias comes from H1/M15 and does not change because three M1 candles went the other way. An M1 move against an intact M5 structure is a **pullback** (or a judas swing you should be fading), never a reversal.

## The setup — six links, all required

This is the canonical ICT sequence compressed to M1. Every link must be present and in this order:

1. **Draw on liquidity defined.** On H1/M15, name the pool price is heading to (PDH/PDL, session high/low, equal highs/lows, an untapped FVG). This is your TP. No named draw → no trade.
2. **Killzone active.** London 02:00–05:00 NY, NY AM 07:00–10:00 NY, Silver Bullet 10:00–11:00 NY. Outside these windows you are flat and idle. Resolve NY time with `zoneinfo`, never a fixed UTC offset — see the runbook.
3. **Liquidity sweep (the manipulation).** A **closed** M1/M5 candle wicks through an obvious pool and closes back inside it. Wick takes the level, body rejects. On M1 gold, sweep the *micro* pool (equal highs/lows of the last 10–20 M1 bars) — sweeping the session extreme puts your stop 8+ points away and kills the RR.
4. **Displacement + MSS (the confirmation).** Within 1–3 M1 bars of the sweep, a candle with `body ≥ 1.5 × ATR(14,M1)` **and** `body/range ≥ 0.5` closes beyond the last opposing M1 swing. Sweep without displacement is not a setup — it is continuation, and you are about to be run over.
5. **PD array to enter from.** The displacement leg leaves a **FVG** (3-candle imbalance) and usually a **bullish/bearish OB** (last opposing-close candle before the leg). Best case that zone also sits in the **OTE** band (0.62–0.79 retrace, sweet spot 0.705) and on the correct side of equilibrium — long in **discount**, short in **premium**.
6. **RR to the draw ≥ 1.5** (prefer ≥ 2.0). Measure it before you send, not after. If the draw is closer than 1.5R, the trade is dead on arrival — skip it. This is deliberately **stricter** than `resolve_target` in `services/data/strategies/ict/_base.py`, which substitutes a synthetic min-RR projection when the pool falls short; here a target with no pool behind it is not a target.

## Two entry models — pick per setup, state which you used

| | **Model A — retrace into the array** | **Model B — market on the MSS close (aggressive default)** |
|---|---|---|
| Entry | Price trades back into the FVG (ideally CE, the 50% of the gap) or OB proximal | Market fill on the close of the displacement candle |
| Stop distance | Tight — entry sits close to the sweep extreme | Wide — you are entering at the far end of the leg |
| Fill rate | ~50%. Displacement legs often never retrace | ~100% |
| Use when | Setup is A-grade but not urgent; the FVG is clean and unmitigated | Killzone is hot, displacement is violent, the draw is far enough to still clear 1.5R |

**The honest tension, in gold points:** a Model B entry with the sweep extreme 5pt behind you needs `SL = 5 + 0.5×ATR ≈ 5.7pt`, so 2R demands an **11.4pt** TP — a real move, not a normal M1 wiggle. Model A on the same setup might enter 3pt lower with a 2.7pt stop and clear 2R on a 5.4pt move. When the draw is close, you *must* use Model A or skip. Do not talk yourself into Model B with a target you cannot reach.

## Grade every setup before sending — A/B/C

Two gates, then five scored factors:

**Gates (fail either → no trade, full stop):** draw on liquidity named · killzone active.

**Factors:** ① sweep quality (clean wick through an obvious pool, body closes back inside) · ② displacement quality (body ≥1.5 ATR, body/range ≥0.5) · ③ PD-array confluence at entry (FVG / OB / OTE — count them) · ④ premium-discount correct for direction · ⑤ RR to the draw ≥ 2.0.

- **A — both gates + 4–5 factors, confluence ≥ 2 arrays.** Full conviction. Eligible for the two-ticket scale-out and for re-arming after a win.
- **B — both gates + 2–3 factors.** Single ticket, Model A entry only, no stacking.
- **C — a gate fails, or ≤1 factor.** Skip. Say "no setup" and wait. Most losses are C setups traded as if they were B.

**Arm ≠ enter.** When the sweep has printed but no displacement has closed yet, the setup is *armed*. Sit on your hands. The trigger is the **close** of the displacement candle, never the price touching a zone.

## Auto SL/TP — compute both, send both

Full math and the fill-verification protocol are in the runbook. The rules you must never violate:

- **SL is structural: beyond the sweep extreme + `0.5 × ATR(M1)` buffer.** The swept wick is the invalidation — if price goes back through it, the manipulation read was wrong.
- **Hard floor on XAUUSDc: SL ≥ 4.0pt from the ask (LONG) / bid (SHORT) at order time**, no matter what the structure says. Tighter than that is a guaranteed stop-hunt on gold. If structure gives you less than 4pt, the setup is too small for M1 gold — skip it.
- **Verify after the fill, immediately.** Price moves 2–4pt during execution. If `|fillPrice − sl| < 2.5pt` → **close now and re-enter** with a correct stop. This is a hard close, not a warning, even if the position is already green.
- **TP is the draw on liquidity**, not a round number of points. If the draw sits below 1.5R, do not shrink the stop to manufacture the ratio — skip the trade.

## Cut rules — every one of these is a market close

The broker SL is an **emergency backstop that should almost never fire**. You exit on premise failure, and premise failure shows up on the chart long before the stop does. Check these every 15–20 seconds while in a position.

**Premise cuts (ICT-specific — these are the ones that matter):**
- **Sweep reclaim.** An M1 candle closes back beyond the swept extreme → the raid was not manipulation, it was continuation. **Cut immediately**, do not wait for the stop.
- **Opposing displacement.** A candle meeting the displacement test (≥1.5 ATR body, ≥0.5 body/range) closes *against* you → smart money is delivering the other way. **Cut.**
- **Entry array violated.** Price closes fully through your entry FVG / beyond the OB's distal edge in the opposite direction → the array is mitigated and dead. **Cut.**

**Drift and emergency cuts:**
- **Two-check adverse rule.** Check 1 shows float negative and check 2 shows it *worse* with no recovery candle forming on M1 → **close now**, do not wait for check 3. This is the single rule that keeps stop-outs off the ledger.
- **Single-check emergency.** Float ≤ **−1.5 points** of adverse move in one check → close, no deliberation. (In USC that is `−1.5 × 100 × lots`; at 0.05 lots, −7.5 USC.)
- **Bad post-fill stop.** `|fill − SL| < 2.5pt` → close and re-enter.

**Time cuts — ICT moves are immediate:**
- **Five M1 bars without covering 50% of the distance to TP** → the displacement premise has expired. Cut at market, flat or small loss.
- **Killzone ends while you are in a trade** → flatten. Delivery outside the window is not what you signed up for. This one is absolute.

**Profit-side closes:**
- **Draw reached** → close at market as price prints into the pool. Do not wait for the TP to fill exactly; liquidity pools reverse hard.
- **Synthetic breakeven (your only substitute for a trailing stop):** once float peaks at **+1.0 point**, if a later check shows it back at **≤ +0.3 point**, close at market. Track this in points, not in the raw USC figure, so it stays correct at any lot size. You cannot move the SL, so the monitoring loop *is* the trail.
- **Wick warning.** In a LONG, an M1 candle with a ≥1.5pt upper wick = sellers absorbing → close for whatever exists. Mirror (lower wick) for shorts.

## Two-ticket scale-out (A-grade only)

Because `/close` cannot go partial, scale out by opening two tickets in the same send:

- **Ticket 1** — `clientTag` suffix `-t1`, TP at the **near** pool or +1.5R, whichever is closer. This one pays for the session.
- **Ticket 2** — suffix `-t2`, TP at the **full draw on liquidity**. This one is the ICT trade.
- **Same SL on both** — one structural level beyond the sweep. When the premise dies, both die together, and every cut rule above closes both tickets.

After ticket 1 closes green, ticket 2 keeps running under the synthetic-breakeven rule. Never open ticket 2 alone with a far TP and no near target — that is how a +200 USC session becomes a −300 USC one.

## Re-arming and stacking

- After a clean close in the direction of the draw, **re-arm immediately** — the same pool usually gets raided more than once, and delivery toward an untapped draw comes in legs.
- Stack a second setup only when: A-grade, same direction as the draw, the prior trade closed green, and the new setup has its **own** fresh sweep. Re-entering on the same sweep is one trade sized up, not two trades.
- **Never average down. Never counter-trade the draw.** If price is being delivered up to BSL, you do not short into it because one M1 candle looked weak.

## Forming-candle trap — the fastest way to lose with this skill

The newest candle from `/candles` is **still forming**; its `close` is the current tick, not a close. Every rule in this skill — sweep, displacement, MSS, array violation — is defined on **closed** candles. Reading a forming candle as closed will hand you fake sweeps and fake shifts all session.

Before acting on any candle: confirm the next candle has already started, or check volume (an M1 gold bar with tick volume < 50 is barely underway). Detail and the failure cases are in the playbook.

## Session stops

- Balance below 50% of session-open balance → stop, tell the user. **Also stop at −10% of session-open balance**: at correct (small) sizing the 50% floor is hundreds of gold points away and will never fire, so it is not a real breaker on its own.
- Two consecutive losses → mandatory pause; wait for a **fresh sweep** in a new killzone before re-entering. Do not re-enter on the same pool.
- Three consecutive losses, no wins between → stop the session, tell the user.
- Killzone closed and nothing armed → flat and idle. This is the normal state most of the day.
- User says stop → stop.

## Output format

Keep it terse and structural — the reasoning is the point.

```
ARM   XAUUSDc  kz=ny_am  bias=LONG(H1)  draw=BSL 3994.80 (M15 eq-highs)
      swept SSL 3986.20 @14:03 (wick 3985.74, closed back in)
ENTRY LONG XAUUSDc  t=<ticket>  fill=3988.31  SL=3985.24 (-3.07)  TP=3994.80 (+6.49)  RR=2.11  model=B  grade=A
CUT   t=<ticket>  exit=3990.90  P/L=+259USC(USD 2.59)  bal=1473USC(USD 14.73)  reason=draw reached (BSL tapped)
```

Session summary: trades, wins/losses, total P/L, start → end balance, %, and **which cut rule fired most** — that last one is where the learning is.

## Journal every trade (CLAUDE.md law)

Every entry gets a reasoning string carrying the full confluence breakdown: killzone · pool swept + level · displacement stats · PD arrays at entry · premium/discount · draw targeted · RR. Same shape the in-pipeline detector emits (see `services/data/strategies/ict/sweep_mss.py`). No silent trades — a trade you cannot explain structurally is a trade you cannot review.

## Validation status — be straight with the user about this

The in-pipeline `ict_sweep_mss` strategy is validated for XAUUSD on **60min only** (15min failed re-validation, 2026-08-13). **M1 SMC/ICT is not backtest-validated on this platform.** The repo's own research (`docs/research/ict-concepts.md` §4) is blunt: ICT is a useful lens on liquidity and time, and an unproven turnkey edge — with ~20 overlapping PD arrays, almost any move can be explained after the fact.

So: run this on **demo/paper first**, keep the journal honest, and if the user asks whether it works, the true answer is that the 60min version cleared validation and this one has not been tested yet. Do not narrate losing trades into "the setup was valid, the market was wrong."

## Editing note — never write a dollar sign immediately followed by a digit

SKILL.md is **argument-interpolated at load time**: a dollar sign followed by a
single digit is replaced by the skill's positional arguments before you ever read
this file. A threshold written as a dollar sign, then one, then point five zero,
silently loads as mangled text when the skill is invoked with arguments — i.e. a
corrupted risk limit in a live trading file, with no error raised.

Write money as `USD 1.50`, `150 USC`, or in points. Keep every dollar sign at
least one character away from a digit. The `references/*.md` files are read as
plain files and are not interpolated, but hold the same convention there so the
figures stay comparable across all three documents.

## Quick reference

Bridge `http://localhost:8800`, header `X-Bridge-Token` from `MT5_BRIDGE_TOKEN`. Symbols via `BROKER_SYMBOL_MAP`: XAUUSD→XAUUSDc, EURUSD→EURUSDc, BTCUSD→BTCUSDc. Endpoints: `/account` `/symbol/{s}` `/candles/{s}` `/order` `/close` `/positions` `/history/{ticket}`. `side` must be `"LONG"`/`"SHORT"`. Payloads and the loop → `references/execution-runbook.md`. Detector rules → `references/smc-ict-m1-playbook.md`.
