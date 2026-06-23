# AI Trading Intelligence Platform
### Roadmap for Advanced Developer, Zero Trading Background

---

## 🧠 Pre-Phase: Trading Fundamentals (Week 1–2)
> You must learn this BEFORE writing a single line of trading logic.

### Core concepts to study:
- **Candlesticks** — OHLCV (Open, High, Low, Close, Volume)
- **Timeframes** — 1m / 5m / 15m / 1h / 4h / Daily and how they relate
- **Trend** — Higher Highs / Higher Lows, structure
- **Support & Resistance** — key price zones
- **RSI** — momentum oscillator (0–100, overbought/oversold)
- **EMA** — Exponential Moving Average (trend direction)
- **ATR** — Average True Range (measures volatility)
- **Risk/Reward (RR)** — e.g. risk $1 to make $2 = 1:2 RR
- **Position sizing** — how much to risk per trade (never >1%)
- **Liquidity sweeps** — price grabs a level then reverses (smart money concept)

### Free resources:
- Babypips.com (forex basics, free course)
- YouTube: "ICT Concepts" for smart money / liquidity
- Investopedia for indicators

---

## 🏗️ Phase 1 — Data Foundation (Week 3–5)
> Goal: Collect, store, and visualize real market data.

### 1.1 Project Setup
```
ai-trading-platform/
├── apps/
│   ├── web/          # Next.js dashboard
│   └── api/          # Node.js backend
├── services/
│   ├── data/         # Python data workers
│   └── ai/           # Python AI/ML service (FastAPI)
├── packages/
│   └── shared/       # shared types/utils
├── docker-compose.yml
└── CLAUDE.md
```

### 1.2 Infrastructure (Docker Compose)
- PostgreSQL — structured data (candles, trades, signals)
- Redis — real-time cache, queues
- TimescaleDB (PostgreSQL extension) — time-series optimization

### 1.3 Data Ingestion
- Sign up: **Alpha Vantage** (free tier) or **Twelve Data**
- Fetch OHLCV candles for: XAU/USD (Gold), EUR/USD, BTC/USD
- Store in `candles` table: `(symbol, timeframe, open, high, low, close, volume, timestamp)`
- Use cron jobs (node-cron or APScheduler) to refresh data

### 1.4 Key Tables to Build
```sql
candles         -- raw price data
indicators      -- RSI, EMA, ATR values
news_events     -- economic calendar events
signals         -- detected trade opportunities
trades          -- executed/simulated trades
journal         -- trade notes + AI reasoning
risk_logs       -- position sizing decisions
```

### ✅ Phase 1 Done When:
- [ ] Docker environment running
- [ ] Candle data flowing into PostgreSQL
- [ ] Can query last 100 candles for any symbol/timeframe

---

## 📊 Phase 2 — Analysis Engine (Week 6–8)
> Goal: Calculate technical indicators from raw price data.

### 2.1 Python Analysis Service
Use `pandas`, `numpy`, `pandas-ta` library:

```python
import pandas_ta as ta

df['rsi'] = ta.rsi(df['close'], length=14)
df['ema_20'] = ta.ema(df['close'], length=20)
df['ema_50'] = ta.ema(df['close'], length=50)
df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
```

### 2.2 Indicators to implement (start simple)
| Indicator | Purpose |
|---|---|
| EMA 20 / 50 / 200 | Trend direction |
| RSI (14) | Momentum / overbought / oversold |
| ATR (14) | Volatility / stop loss sizing |
| Support/Resistance zones | Key price levels |
| Higher High / Lower Low detection | Trend structure |

### 2.3 Dashboard — Phase 2
- Next.js page showing:
  - TradingView widget (embed free chart)
  - Indicator values in real-time
  - Symbol switcher (Gold, EUR/USD, BTC)

### ✅ Phase 2 Done When:
- [ ] RSI, EMA, ATR calculated and stored
- [ ] Dashboard shows live chart + indicator values

---

## 🤖 Phase 3 — AI Intelligence Layer (Week 9–12)
> Goal: Add Claude/GPT to reason about market conditions.

### 3.1 Market Context Summarizer
Call Anthropic API with:
```
Given this market data:
- Symbol: XAU/USD
- 4H Trend: Bullish (price above EMA 50)
- RSI: 58 (neutral-bullish)
- ATR: 12.4 (moderate volatility)
- Recent news: Fed held rates, dollar weakened

Provide a concise market context summary and bias (Bullish/Bearish/Neutral).
```

### 3.2 News Analyzer
- Source: **NewsAPI**, **ForexFactory calendar**, **Alpha Vantage news**
- Feed high-impact events (NFP, CPI, FOMC) to Claude
- Claude returns: sentiment, expected impact, affected pairs

### 3.3 Trade Validator
When a signal is detected, AI checks:
- Does this trade align with higher timeframe trend?
- Is RR acceptable (>1:2)?
- Are there upcoming news events that could invalidate?
- Output: Score 0–100 + reasoning

### 3.4 Journal AI Reviewer
Weekly: Feed all journal entries to Claude
- Detect patterns in losses
- Identify emotional decision flags
- Suggest improvements

### ✅ Phase 3 Done When:
- [ ] Market summary generated on dashboard
- [ ] News events analyzed automatically
- [ ] Signal validation returns AI reasoning

---

## 📋 Phase 4 — Strategy + Backtesting Engine (Week 13–16)
> Goal: Build deterministic rules. Validate with history. This is critical.

### 4.1 Signal Detection Rules (start with 1 strategy)
```
Strategy: EMA Pullback + RSI Confirmation

IF:
  - 4H EMA 20 > EMA 50 (bullish trend)
  - Price pulls back to EMA 20
  - RSI on 15m dips to 40–50 zone
  - RSI divergence (price lower, RSI higher)
  - Risk/Reward >= 1:2

THEN:
  generate_signal(direction=LONG, confidence=HIGH)
```

### 4.2 Backtesting Framework
- Run strategy against 2–3 years of historical candles
- Track: win rate, average RR, max drawdown, Sharpe ratio
- Never live trade a strategy with <100 backtest trades

### 4.3 Key Metrics to Track
| Metric | Target |
|---|---|
| Win Rate | >45% (if RR is 1:2+) |
| Max Drawdown | <15% |
| Profit Factor | >1.5 |
| Sharpe Ratio | >1.0 |

### ✅ Phase 4 Done When:
- [ ] At least 1 strategy backtested with 100+ trades
- [ ] Results logged and visualized in dashboard
- [ ] Strategy statistically validated

---

## ⚠️ Phase 5 — Risk Engine (Week 17–18)
> Most important layer. Build this before ANY execution.

### 5.1 Position Sizing Formula
```python
def position_size(account_balance, risk_pct, entry, stop_loss):
    risk_amount = account_balance * risk_pct  # e.g. 1%
    pips_at_risk = abs(entry - stop_loss)
    return risk_amount / pips_at_risk
```

### 5.2 Hard Rules (non-negotiable)
- Max risk per trade: **1%** of account
- Max daily loss: **3%** — system pauses for the day
- Max drawdown: **10%** — system stops until reviewed
- No trades during major news (30 min before/after)
- Minimum RR: **1:2**

### ✅ Phase 5 Done When:
- [ ] Position size auto-calculated for every signal
- [ ] Daily loss circuit breaker implemented
- [ ] Risk log stored for every decision

---

## 🚀 Phase 6 — Paper Trading + Execution (Week 19–22)
> Paper trade FIRST. Real money only after 1 month of paper results.

### 6.1 Paper Trading Mode
- Simulate trades with fake money
- Full execution logic, risk engine, journaling active
- Run for minimum 4 weeks, minimum 30 trades

### 6.2 Broker API (when ready for real)
- **OANDA** — best for Forex/Gold, good API, low minimum
- **Interactive Brokers** — more complex, more markets

### 6.3 Execution Safety Layer
```
Signal Generated
    ↓
AI Validator (score > 70?)
    ↓
Risk Engine (position size, daily limit check)
    ↓
Human Confirm (early stage) OR Auto Execute (later)
    ↓
Broker API
    ↓
Journal Entry Created
```

### ✅ Phase 6 Done When:
- [ ] 30+ paper trades executed
- [ ] Win rate and drawdown within expected range
- [ ] Ready for small real capital ($500–$1000 max)

---

## 📡 Phase 7 — Monitoring + Observability (Ongoing)
- Real-time dashboard: open trades, P&L, drawdown meter
- Alerts: Telegram/Discord bot for signals + risk breaches
- Weekly AI report: performance review, pattern analysis
- Grafana + Prometheus for system health

---

## 🗂️ Your CLAUDE.md (Start With This)

```markdown
# AI Trading Intelligence Platform

## Stack
- Next.js 14 (dashboard frontend)
- Node.js + Express (main API)
- Python + FastAPI (analysis + AI service)
- PostgreSQL + TimescaleDB (historical data)
- Redis (real-time cache)
- Docker Compose (local dev)

## Key Domains
- Market data ingestion (candles, news)
- Technical indicator calculation (pandas-ta)
- AI reasoning layer (Anthropic API)
- Strategy engine (deterministic rules)
- Risk management (position sizing, circuit breakers)
- Execution engine (broker API)

## Rules
- Risk engine is ALWAYS called before any execution
- Never hardcode API keys — use .env
- All trades must be journaled
- Backtest before any live strategy
- Paper trade before real money
```

---

## 🎯 Summary Timeline

| Phase | Focus | Duration |
|---|---|---|
| Pre-Phase | Learn trading basics | 2 weeks |
| Phase 1 | Data collection + storage | 3 weeks |
| Phase 2 | Analysis engine + dashboard | 3 weeks |
| Phase 3 | AI intelligence layer | 4 weeks |
| Phase 4 | Strategy + backtesting | 4 weeks |
| Phase 5 | Risk engine | 2 weeks |
| Phase 6 | Paper trading → live | 4+ weeks |
| Phase 7 | Monitoring (ongoing) | ongoing |

**Total to first paper trade: ~4–5 months**
**Total to first real trade: ~6 months**

> ⚡ With Claude Code helping you build each phase, you can move faster — but never skip validation steps.
