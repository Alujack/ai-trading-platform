# Aggressive Scalping Playbook (Account Flip)

High-frequency, momentum-pressing scalping aimed at fast account growth through compounding. Claude is the analysis brain — the user or MT5 bridge handles execution. Read alongside `../../scalping-trader/references/scalping-playbook.md` for core fundamentals. This file covers what's different in aggressive/flip mode.

## The honest math of flipping

A flip (e.g. $12 → $25) is compounding many small edges fast. Two levers:

- **Risk per trade** (hard cap 5%). At 3% risk, 1.3:1 avg win, 55% hit rate — expectancy is positive but variance is brutal. A 6-trade losing streak (~17% drawdown) is statistically common. Daily-loss breaker stays ON.
- **Frequency + compounding.** On micro accounts: faster reps with minimum lots. On larger accounts: size = `balance × risk% / stopDistance`, recomputed each trade. Don't manually oversize.

Reality check: even with an edge, the probability of doubling before halving at 3–5% risk is far from certain. Most flip attempts go to zero. This is the acknowledged risk.

## Micro-account sizing (< $100, e.g. $12 demo)

Standard lot math: `lots = (balance × risk%) / (stopPts × $perPtPerLot)`

For XAUUSDm on Exness:
- Contract: 100 oz | min lot: 0.01 | tickValue: $0.10 per 0.001 pt
- **$1 per point per 0.01 lot**
- 6-point stop + 0.01 lots = $6 risk

At $12.26 balance, 0.01 lots + any reasonable stop = 50–80% account risk. **This is over the 5% rule.** Flag it explicitly, note it's demo, let the user decide. The flip logic on micro accounts = tightest possible stop (1–1.5× ATR), smallest target (1–1.5× ATR), maximum reps.

For larger accounts (> $200): standard engine math applies. Let the engine size it.

## RiskConfig — set once per session

```
PUT /api/config/risk
{
  "scope": "GLOBAL", "scopeKey": "",
  "riskPerTradePct": 3,
  "minRR": 1,
  "dailyLossLimitPct": 5,
  "maxDrawdownPct": 15,
  "maxOpenTrades": 8,
  "aiMinScore": 55
}
```

| Field | Calm | Aggressive | Engine bound |
|---|---|---|---|
| riskPerTradePct | 1 | 3–5 | max 5 |
| minRR | 2 | 1–1.5 | min 1 |
| dailyLossLimitPct | 3 | 5 (keep tight) | max 50 |
| maxDrawdownPct | 10 | 15 | max 100 |
| maxOpenTrades | 5 | 8 | max 100 |
| aiMinScore | 70 | 55 | 0–100 |

## Live data — always use the MT5 bridge

DB candles lag 5–15 min. **Always pull fresh candles from the bridge for the actual setup read:**

```
GET /candles/{symbol}?timeframe=M5&count=10   # last 10 × 5min candles
GET /candles/{symbol}?timeframe=M1&count=15   # last 15 × 1min candles
GET /symbol/{symbol}                          # live bid/ask + spread
GET /account                                  # balance, equity, free margin
GET /positions                                # open positions
```

Use DB candles (`GET /api/candles?symbol=XAUUSD&timeframe=5min&limit=5`) only for EMA/RSI/ATR indicator values — these are computed by the platform and not available from the bridge.

**Before any trade: check spread.** Wide spread (XAUUSDm > 0.50 pts, EURUSDm > 0.0003) = skip the rep.

## Setups — press momentum, don't predict

Trade WITH the 5min EMA trend. EMA stack = EMA20 vs EMA50:
- EMA20 > EMA50 → bias LONG
- EMA20 < EMA50 → bias SHORT
- Never trade counter-trend in aggressive mode. That's the fastest way to blow a flip.

### 1. Breakout press (highest probability)
1. Tight 1–2 candle consolidation at/with the trend direction on 1min or 5min.
2. Decisive break of the consolidation high/low with expanding candle body + volume.
3. Entry: on the break candle close OR first shallow retest that holds.
4. Stop: just back inside the range (1× ATR max). Target: 1.5–2× ATR from entry.
5. If break fails immediately (inside bar back inside range within 1 candle): exit at break-even or small loss. Don't hold.

### 2. Pullback continuation
1. Strong trending move visible on 5min (EMA20 > EMA50, RSI > 50 for longs).
2. Shallow pullback to EMA20 that holds (RSI doesn't cross 50).
3. Entry: resumption candle (close back in trend direction).
4. Stop: below the pullback low (for long) or above high (for short), 1× ATR.
5. Target: 1.5× ATR from entry, or prior swing high/low.
6. Optional add: second clip on next continuation IF first clip's stop is at breakeven and total open risk stays within caps.

### 3. Momentum flip (EMA50 rejection)
Used when price approaches a key EMA level with exhaustion signals:
1. Strong push into EMA50 (or EMA200) on declining volume.
2. RSI diverging (making lower high while price makes higher high, or vice versa) OR candle wicks rejecting the level on 2+ candles.
3. Entry: after the rejection candle closes back away from the level.
4. Stop: 1.1× ATR beyond the rejection extreme (not arbitrary — must be ATR-based for AI gate).
5. Target: 1.5× ATR in the new direction. Fast exit — momentum flips are short-lived.
6. If price reclaims the level within 1 candle: time-stop immediately.

**Learned from session:** A failed EMA50 breakout (price breaks above EMA50, high volume on breakout candle, then closes back below EMA50 on low volume) is a strong short signal. The low-volume close back at EMA50 = momentum exhaustion. Scored 85/100 through the AI gate.

## Scaling-in rules

- Only add when the first position's stop is at breakeven or better.
- Total combined risk must stay within `maxOpenRiskPct` — engine enforces it.
- **Never add to a loser.** Averaging down = account-killer #1 in flip mode.
- Move the combined stop up together; one momentum failure exits all clips.

## Trade card format (always show this before placing)

```
SELL/BUY XAUUSDm
Entry:   4008.13  (market fill)
SL:      4016.00  (+7.87 pts risk)
TP:      3999.00  (-9.13 pts reward)
Lots:    0.01
RR:      1.16:1
$ Risk:  ~$7.87  (64% of $12.26 — demo, flagged)
$ Reward: ~$9.13
```

## Execution via MT5 bridge

```
POST /order
{
  "symbol": "XAUUSDm",
  "side": "sell",          # "buy" or "sell"
  "lots": 0.01,
  "sl": 4016.000,
  "tp": 3999.000,
  "clientTag": "flip-xauusd-short-YYYYMMDD"
}
# Response: { "status": "filled", "ticket": 12345678, "fillPrice": 4008.125 }
```

Close a position:
```
POST /close  { "ticket": 12345678 }
```

Always check `GET /symbol/{symbol}` for live bid/ask immediately before sending the order — price moves while you're analyzing.

## Session & spread rules

- **Primary window**: London/NY overlap 12:00–16:30 UTC (XAU/EUR). This is when volume and directional moves are largest.
- **After 16:30 UTC**: US session only — still tradeable for XAU/BTC, but volume thins. Spreads widen. Use tighter stops and smaller targets.
- **Avoid**: Asian session for XAU (spreads blow out, moves are choppy).
- **News**: ±30 min around HIGH-impact USD/Gold events — stand aside completely.

## Trade management (aggressive style)

- **Entry**: market order for speed. Don't use limit orders chasing exact price — you'll miss the move.
- **Breakeven**: move SL to entry once trade is ~40–50% of the way to TP.
- **Time-stop**: if trade is open 3+ candles (5min × 3 = 15 min) with no meaningful movement toward target — exit at market. A stale trade is a dead trade.
- **Early exit**: if the setup structure breaks (price closes back through the key level the setup was based on) — exit at market, don't wait for SL.
- **Never widen SL** once placed. If it's wrong, take the loss and reset.

## Protecting the flip (banking it)

- Daily-loss breaker trips → session OVER. No exceptions, no "one more trade."
- After a strong green run (+30–50% of balance): step risk back down to 1–2%.
- Once original stake is recovered: explicitly lower risk% to protect gains. The engine still sizes off the full balance — you must manually reduce riskPerTradePct via `PUT /api/config/risk`.
- **Walk away at target.** More reps is not always better. Banking a flip is harder than making it.

## Pre-trade checklist (every rep, 30 seconds)

1. Liquid session, spread normal (check `GET /symbol/{symbol}`)?
2. EMA stack clear in trade direction?
3. Setup matches a recipe above?
4. ATR-based stop defined, TP ≥ 1.5:1 (for AI gate) or ≥ 1:1 (for bridge-only)?
5. Inside daily-loss limit (check balance vs peak)?
6. No open position in same symbol that stacks correlated risk?

Any "no" → skip the rep. There's always another candle.

## Sources
- TradeZella — scalping strategies, exits, partials: https://www.tradezella.com/blog/scalping-strategies
- Warrior Trading — 1-minute momentum scalping: https://www.warriortrading.com/1-minute-scalping-strategy/
- Opofinance — risk management for scalping / drawdown math: https://blog.opofinance.com/en/risk-management-tactics-for-scalping/
- FXOpen — 1m scalping & ATR multipliers: https://fxopen.com/blog/en/1-minute-scalping-trading-strategies-with-examples/
