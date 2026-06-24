# Scalping Playbook

Professional scalping knowledge, distilled into rules this platform can act on. Synthesized from established intraday practice (see Sources at end). Apply judgement — these are defaults, not laws. The platform's risk engine has the final say on every number.

## What scalping is (and the mindset)

Scalping = many small, fast trades capturing tiny moves (~0.05%–0.2% each), holding seconds to a few minutes. Active scalpers may take dozens of trades a session; forex scalpers average ~87 trades/day with ~2–3 min median holds. Profit comes from a small statistical edge repeated with iron discipline, NOT from being right about big direction. Consequences:

- **Selectivity beats activity.** Only the cleanest setups. Most bars are noise -> "no trade."
- **Speed and rules over emotion.** Pre-decide entry, stop, target. Exit the instant the thesis breaks; never widen a stop.
- **Costs matter enormously.** Spread + slippage + commission eat a large share of a tiny target. Only trade when spreads are tight (liquid hours). Account for costs in backtests (`noCosts: false`).
- **Daily loss limit is sacred.** Stop at the platform's 3% daily cap. Revenge trading is the #1 account killer.

## Session timing (UTC) — trade liquidity, avoid the lulls

Scalping needs liquidity (tight spreads) AND movement. The two coincide during session overlaps.

- **PRIME — London/NY overlap ~12:00–16:00 UTC.** Best for XAUUSD and EURUSD: deepest liquidity, tightest spreads, cleanest directional moves. Gold sets its daily high/low in this window ~70% of the time. Default scalping window.
- **Good — London open ~07:00–10:00 UTC** and **NY open ~13:30–15:30 UTC**: strong volatility expansions.
- **AVOID — Asian lull / post-NY ~22:00–00:00 UTC**: thin liquidity, wide spreads, choppy. Skip unless the user insists.
- **Asian session (00:00–07:00 UTC)**: generally too slow for FX/gold scalps; ranges are tighter. BTCUSD trades 24/7 but still scalps best during US/EU active hours.

Always check current UTC (`date -u`) before declaring a setup. Wrong session = no edge.

## News — the stop killer

High-impact releases (NFP, FOMC, CPI, rate decisions) can move XAUUSD 300–1000+ pips and blow gold spreads past 50 pips. The risk engine blocks trades within +/-30 min of HIGH-impact events (`newsBeforeMin`/`newsAfterMin`). Reinforce this:
- Don't open scalps just before scheduled high-impact data.
- If volatility/spread is abnormally wide, cut size or stand aside.
- "News scalping" (trading the spike) is a different, higher-risk game — only with explicit user intent and reduced size.

## Indicator settings for scalping

The platform stores RSI-14 and EMA 20/50/200 by default — fine for 15min context and trend bias. For tighter 1min/5min triggers, faster settings are standard; compute from raw candles or pass as strategy params.

| Indicator | 15min context (stored) | 5min scalp | 1min scalp |
|-----------|------------------------|------------|------------|
| Fast EMA  | EMA20                  | EMA5–EMA9  | EMA9       |
| Slow EMA  | EMA50                  | EMA13      | EMA15      |
| Trend bias| EMA200                 | EMA50/200  | EMA50      |
| RSI       | RSI-14                 | RSI-7      | RSI-2/3 (very fast) |
| ATR       | ATR-14 (stops)         | ATR-14     | ATR-14     |

- **EMA**: alignment = trend. Fast above slow above bias = uptrend (longs only), and vice versa. Pullbacks to the fast EMA are entry zones.
- **RSI**: momentum filter, not a standalone trigger. Avoid longs when RSI already overbought (>70) / shorts when oversold (<30). On faster periods RSI swings hard — use as confirmation of the turn, not the whole reason.
- **ATR**: the volatility ruler for stops. Stop distance scales with ATR so the trade adapts to current conditions. For 1min, a ~1.5x ATR multiplier is common (the default 2x can be too wide and miss entries); 5min often ~1.5–2x.

## Setup recipes

### A. Trend pullback (primary — matches scalp_ema)
Direction: with the trend only.
1. Trend aligned: fast EMA > slow EMA > bias EMA (long) or inverse (short), on the scalp timeframe.
2. Price pulls back to/near the fast EMA and holds (rejection wick / momentum candle).
3. RSI turns back up from the midline (long) / down (short) — confirming the pullback is ending, not overbought.
4. Entry: on the close of the confirmation candle.
5. Stop: ~1.2–2.0x ATR beyond the swing/EMA (below for long).
6. Target: >= 1:2 RR (platform minimum). Optionally bank partial at 1:1 and trail the rest.

This is essentially what `services/data/strategies/scalp_ema.py` encodes — start from it for new variants.

### B. Range / mean reversion (when no trend)
Use only in a clear, established range during liquid hours.
1. Price stretches to the range extreme (and/or RSI overbought/oversold).
2. Rejection candle at the boundary.
3. Entry toward the range middle; stop just beyond the extreme; target the mid/opposite band, RR >= 1:2.
Skip if a trend is in force — fading a strong trend is how scalpers get run over.

### C. Breakout (range expansion)
1. Tight consolidation during a liquid window.
2. Decisive break + retest holds.
3. Entry on the retest; stop back inside the range; target a measured-move that clears 1:2.
Beware false breaks in thin hours — this needs the liquid window.

## Stops, targets, exits

- **Hard stop on every entry, always.** No mental stops, no widening. The risk engine enforces RR but YOU define a sane stop.
- **ATR-based stops** adapt to volatility — preferred over fixed pips when ATR is available.
- **Minimum 1:2 RR** or the gate rejects. Many scalpers bank a partial at 1:1 and trail the remainder; the platform's monitor handles fixed TP/SL.
- **Exit when the thesis breaks**, not only at the stop (e.g., trend structure flips). Time-stop stale trades that go nowhere — a scalp that isn't working in a few minutes usually won't.

## Position sizing & risk math

- Risk a **fixed small % per trade** (platform default 1%, `PAPER_RISK_PERCENT`). Size = `balance * risk% / |entry - stop|`. The risk engine computes this — don't override.
- Keep total open risk and per-currency risk within caps; don't run several correlated USD trades at once.
- **Daily stop 3%**: once hit, done for the day. Recommend the user walk away.

## Pre-trade checklist (run mentally every time)

1. Liquid session right now? (UTC checked)
2. No high-impact news inside +/-30 min?
3. Trend/range regime identified, setup matches a recipe?
4. Entry, ATR-based stop, and >= 1:2 target defined with numbers?
5. Spread/volatility normal (not blown out)?
6. Within daily loss limit and open-trade caps?
If any answer is no -> no trade, and say why.

## Sources
- TradeZella — Scalping strategies with entry/exit rules: https://www.tradezella.com/blog/scalping-strategies
- Warrior Trading — 1-minute scalping strategy: https://www.warriortrading.com/1-minute-scalping-strategy/
- Opofinance — Risk management tactics for scalping: https://blog.opofinance.com/en/risk-management-tactics-for-scalping/
- MC2 Finance — Best RSI for scalping: https://www.mc2.fi/blog/best-rsi-for-scalping
- FXOpen — 1-minute scalping strategies & ATR multiplier: https://fxopen.com/blog/en/1-minute-scalping-trading-strategies-with-examples/
- NordFX — Best time to trade gold (sessions/volatility/news): https://nordfx.com/traders-guide/best-time-to-trade-gold-xauusd-sessions-volatility-news
- EBC — Best XAUUSD trading hours: https://www.ebc.com/forex/what-are-the-best-xauusd-trading-hours
