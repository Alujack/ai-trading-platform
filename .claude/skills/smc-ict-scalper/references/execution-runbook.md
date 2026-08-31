# Execution Runbook — MT5 Bridge, Gold Math, The Loop

Everything you need to actually place and manage an SMC/ICT M1 trade. Payload shapes verified against `services/mt5bridge/app.py`.

---

## 1. Connection

```
Base:   http://localhost:8800          # MT5_BRIDGE_URL is host.docker.internal:8800 from inside containers
Header: X-Bridge-Token: <MT5_BRIDGE_TOKEN from .env>
```

Symbol map (`BROKER_SYMBOL_MAP` in `.env`): `XAUUSD→XAUUSDc`, `EURUSD→EURUSDc`, `BTCUSD→BTCUSDc`. Always send the **c-suffixed** broker symbol to the bridge.

Endpoints — this is the complete surface:

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /health` | connection check | run once at session start |
| `GET /account` | balance, equity, marginFree, leverage | session-start and after each close |
| `GET /symbol/{s}` | bid, ask, digits, point, volumeMin/Step, tickValue | **call immediately before every order** |
| `GET /candles/{s}?timeframe=&count=` | OHLCV | `timeframe`: `1min`/`M1`, `5min`/`M5`, `15min`/`M15`, `60min`/`H1`, `daily`/`D1`. `count` 1–5000 |
| `POST /order` | market entry | sl **and** tp are required fields |
| `POST /close` | full market close | whole ticket only, no partial |
| `GET /positions` | open positions with live float | the management heartbeat |
| `GET /history/{ticket}` | realized deals for a closed ticket | post-session review |

(`POST /session/login` and `GET /candles_range/{s}` also exist — account switching and deep history paging. Neither is used in a scalping session.)

**There is no modify endpoint.** No trailing stop, no move-to-breakeven, no TP adjustment. Plan accordingly.

**`/symbol` does not expose `trade_stops_level`** — the broker's minimum stop distance. The 4.0pt floor below is comfortably clear of a typical gold stops-level, but if `POST /order` comes back `rejected` with retcode 10016 (`INVALID_STOPS`), that is the cause: widen SL/TP and resend, do not retry the same payload.

### Candle response shape

```json
[{"timestamp": 1756654920, "open": 3986.42, "high": 3987.11,
  "low": 3986.05, "close": 3986.88, "volume": 214}, ...]
```

Oldest first; `timestamp` is Unix seconds (UTC). **The last element is the forming bar** — evaluate structure on `candles[:-1]`.

---

## 2. Clock — resolve the killzone properly

DST-correct NY time, matching `killzones.py`:

```bash
python -c "from datetime import datetime,timezone; from zoneinfo import ZoneInfo; \
n=datetime.now(timezone.utc); print('UTC',n.strftime('%H:%M'),'| NY',n.astimezone(ZoneInfo('America/New_York')).strftime('%H:%M %Z'))"
```

Windows (NY local, end-exclusive): **london** 02:00–05:00 · **ny_am** 07:00–10:00 · **silver_bullet** 10:00–11:00.

Check the clock **before** analysis, not after — if no window is open, say so and stop. Also check it every loop: the killzone-expiry cut is absolute and it fires on the clock, not on the chart.

---

## 3. Gold math — XAUUSDc on a CENT account

**This is an Exness CENT account (Real25). Balance and every `profit` field are
denominated in USC, where 100 USC = $1.00.** Verified live from `GET /symbol/XAUUSDc`:

| Quantity | Value |
|---|---|
| Contract size | **1 oz** (1/100 of a standard lot — cent account) |
| digits / point | 3 / 0.001 |
| tickValue | 0.1 USC per 0.001 move per 1.00 lot |
| **1 point (1.00 price move) @ 0.01 lot** | **1 USC = $0.01** |
| **1 point (1.00 price move) @ 1.00 lot** | **100 USC = $1.00** |
| Min lot / step | 0.01 / 0.01 |
| M1 ATR(14), active killzone | **~1.5–2.5 points** (measured 2.31 at gold 4422) |
| Typical spread, killzone | 0.15–0.35 points (measured 0.26) |

Never read a raw `profit`/`balance` number as dollars — divide by 100. A "float
of −1.50" from `GET /positions` is −1.50 USC = −$0.015, and it corresponds to
1.5 points of adverse move only at 0.01 lots.

**Position sizing — derive it, do not assume 0.01.** With the skill's 4.0pt
minimum stop, risk per ticket = `4 × 100 × lots` USC. Keep that at 1–3% of
balance:

```
lots = (balance_USC * risk_pct) / (stop_points * 100)
```

| Balance | 2% risk, 4pt stop | Notes |
|---|---|---|
| 1,214 USC (~$12) | **0.05–0.06 lots** | current account |
| 5,000 USC (~$50) | 0.25 lots | |
| 10,000 USC (~$100) | 0.50 lots | |

**Sanity check before every send:** `balance_USC / (100 × lots)` = how many gold
points wipe the account. At 1.00 lot on a 1,214 USC balance that is **12.1
points** — less than gold's normal hourly range, so 1.00 lot is an account-ending
size here regardless of how good the setup looks. Two-ticket scale-out doubles
exposure, so halve the per-ticket size when you use it.

### Stop placement

```
LONG:   sl_structural = sweep_low  − 0.5 * ATR(M1)
SHORT:  sl_structural = sweep_high + 0.5 * ATR(M1)

LONG:   sl_floor = ask − 4.0        SHORT:  sl_floor = bid + 4.0
LONG:   sl = min(sl_structural, sl_floor)    # whichever is FURTHER from price
SHORT:  sl = max(sl_structural, sl_floor)
```

The 4.0pt floor exists because fills arrive 1–2pt off the quoted price in fast conditions and gold routinely spikes 2.5pt inside a 15-second monitoring gap. A structurally "correct" 2pt stop on M1 gold is a donation.

If the 4.0pt floor pushes RR below 1.5, **the setup is too small for M1 gold — skip it.** Do not shrink the stop to rescue the ratio.

### Target

```
tp = draw_on_liquidity                       # a named pool, never a point count
rr = |tp − entry| / |entry − sl|
require rr >= 1.5   (prefer >= 2.0)
```

With a 4pt stop that means a **≥6pt** TP (≥8pt at 2R). Sanity-check against the session: if gold has been ranging 4pt an hour, that target is not arriving inside a killzone. Skip.

### Post-fill verification — run this within 10 seconds of every fill

```
if abs(fillPrice − sl) < 2.5:   POST /close immediately, re-enter with a correct stop
```

Hard close, not a warning — even if the position is already green. The stop distance erodes as fast as the profit builds, and the adverse spike always arrives faster than the next check.

---

## 4. Placing the order

```http
POST /order
{
  "symbol":    "XAUUSDc",
  "side":      "LONG",                       // "LONG" | "SHORT" — NOT "buy"/"sell"
  "lots":      0.01,
  "sl":        3985.240,
  "tp":        3994.800,
  "clientTag": "ict-xau-long-20260831-1403-t1",
  "deviation": 20                            // optional; default from bridge
}
```

Response: `{"status":"filled","ticket":123456,"fillPrice":3988.31}` or `{"status":"rejected","reason":"..."}`.

**`clientTag` is an idempotency key.** A repeat of the same tag returns the existing position (`reason:"idempotent_existing"`) instead of opening a second one — which is exactly what you want on a retry, and exactly what breaks the two-ticket scale-out if you forget the `-t1`/`-t2` suffixes. Tag format: `ict-{sym}-{dir}-{YYYYMMDD}-{HHMM}-{t1|t2}`.

Note the bridge only sends **market** orders (`TRADE_ACTION_DEAL` at the current bid/ask). A Model A "retrace into the FVG" entry is therefore *you waiting* until price is inside the array, then sending a market order — there is no resting limit.

Always `GET /symbol/{s}` immediately before sending. Gold moves 3–5pt per minute in a killzone; a quote from 40 seconds ago is fiction.

### Two-ticket scale-out (A-grade only)

Two `POST /order` calls, same `sl`, different `tp`, different tag suffix:

```
t1: tp = near pool or entry ± 1.5R      (whichever is closer)
t2: tp = full draw on liquidity
```

Every cut rule applies to **both** tickets — when the premise dies you send two `/close` calls.

---

## 5. Closing

```http
POST /close
{ "ticket": 123456 }
```

Response: `{"status":"closed","ticket":123456,"exitPrice":3990.90,"profit":2.59}`. Other statuses: `not_found` (already closed — TP/SL fired), `error` with a retcode.

`profit` is the realized figure summed from history deals, so it is the number to journal. Note the ~2-second execution delay: at a liquidity pool, price can reverse 1.5–2pt inside that window. When entry is within ~3pt of a strong pool, prefer letting the **TP limit** fill (instant, no slippage) over a manual market close.

---

## 6. The loop

**Session start (once):**
1. `GET /health`, `GET /account` — record the opening balance; the 50% floor is computed from it.
2. Clock check → which killzone, how many minutes left in it.
3. `GET /candles/XAUUSDc?timeframe=daily&count=3` → PDH/PDL.
4. `GET /candles/XAUUSDc?timeframe=60min&count=12` → session high/low, H1 bias, the draw.
5. `GET /candles/XAUUSDc?timeframe=15min&count=40` → M15 swings, equal highs/lows, equilibrium of the active leg.
6. State the **narrative** out loud before trading: bias, draw, the pool you expect to be raided first.

**Flat, in a killzone (every 20–30s):**
1. `GET /candles/XAUUSDc?timeframe=1min&count=40` — evaluate on `[:-1]`.
2. Update the micro-pool map (M1 equal highs/lows, last 10–20 bars).
3. Sweep printed on a closed bar? → **ARM**, name the pool and level.
4. Armed → watch for the displacement + MSS close (must land within 1–3 bars, else disarm).
5. Grade A/B/C. C → say "no setup", keep looping.
6. `GET /symbol/XAUUSDc` → compute sl/tp/RR → `POST /order`.

**In a position (every 15–20s — not 30–60s):**
1. **Immediately after the fill** (within 10s): `GET /positions` + `GET /symbol` — run the post-fill SL check and get the first float reading. Do not wait for the next iteration.
2. `GET /positions` → float, and `GET /candles?timeframe=1min&count=10` → the premise checks.
3. Run the cut ladder in order:
   - sweep reclaimed (closed bar beyond the swept extreme) → **close**
   - opposing displacement closed → **close**
   - entry array violated on a close → **close**
   - float worse than the prior check with no recovery candle → **close**
   - float ≤ −1.5 points adverse → **close**
   - `|fill − sl| < 2.5pt` → **close**
   - 5 M1 bars, less than 50% of the way to TP → **close**
   - killzone window ended → **close**
4. Profit side: draw reached → close · float peaked at +1.0 point then fell to ≤ +0.3 point → close · ≥1.5pt wick against you → close.
5. After a close: `GET /account`, log the line, re-arm if the draw is intact and a fresh sweep appears.

**Killzone ends:** flatten everything, print the session summary, go idle. Do not "just watch one more setup."

---

## 7. Failure modes worth memorizing

| Symptom | Cause | Fix |
|---|---|---|
| Entry immediately runs against you, SL fires | Traded a *break* as a sweep (candle closed beyond the pool) | Sweep requires the body to close back inside |
| Setup looked perfect, price never came back | Model A on a violent displacement leg | Model B on A-grade in a hot killzone; accept the wider stop or miss it |
| Stopped out then price ran your way | SL under 4pt / under 2.5pt post-fill | Enforce the floor; close-and-re-enter on a bad post-fill distance |
| Float peaks at +2 points and closes negative | No trail exists on this bridge | Synthetic breakeven: +1.0 point peak, close at ≤ +0.3 point |
| TP never fills, trade bleeds out | TP was a point count, not a pool | TP must be a named liquidity level |
| Sweep and MSS both "print", then vanish | Read the forming bar | Evaluate `candles[:-1]`; check volume ≥50% of the 10-bar mean |
| Good session turns red after 11:00 NY | Traded outside the killzone | Killzone expiry is an absolute flatten |

---

## 8. Session log line format

```
ARM   XAUUSDc  kz=ny_am  bias=LONG(H1)  draw=BSL 3994.80 (M15 eq-highs, untapped)
      swept SSL 3986.20 @14:03 (wick 3985.74, close 3986.51 back inside)
      MSS @14:05 body=1.9pt (1.6xATR) br=0.71 broke swing 3988.05
ENTRY LONG XAUUSDc t=123456 fill=3988.31 SL=3985.24(-3.07) TP=3994.80(+6.49) RR=2.11 model=B grade=A
CUT   t=123456 exit=3990.90 P/L=+259USC(USD 2.59) bal=1473USC(USD 14.73) reason=draw reached
```

Session summary: trade count · wins/losses · total P/L · start → end balance · % · **most-fired cut rule**. That last field is the session's actual lesson — if "two-check adverse" dominates you are entering late; if "killzone expiry" dominates you are entering too near the close of the window.

---

## 9. Related config (pipeline path only — not used by this skill)

If the user later wants this running through the platform instead of direct-to-bridge, the ICT detectors take these params (`services/data/strategies/ict/_base.py`): `swingK` (2), `atrBuffer` (0.5), `minRr` (2.0), `sweepLookback` (5), `cooldownMs` (3600000), `aiMinScore` (70), `lookback` (80). That path adds the risk engine, the AI gate, and journaling automatically — and requires a backtest first, per CLAUDE.md. The direct-bridge path in this skill has none of those guardrails, which is exactly why its session stops are non-negotiable.
