# Forex Trading Strategy Survey

**A comparative, cited research report for the AI Trading Intelligence Platform**

*Prepared: 22 June 2026 · Scope: trend-following, mean reversion, breakout/volatility, and carry trade · Audience: builders of an automated forex trading system*

---

## How to read this report

This is a survey of the four major forex strategy families. For each, it covers how the strategy works, concrete entry/exit logic and the indicator parameters practitioners actually use, the market regimes where it wins versus where it fails, its risk/reward profile, the empirical evidence, and the key risks. A cross-cutting section on backtesting, risk management, and realistic expectations follows, because those concerns apply to every strategy and are where most automated systems quietly fail.

A blunt framing worth stating up front: regulators require forex/CFD brokers to disclose that the large majority of retail accounts lose money — ESMA-jurisdiction disclosures cluster around 74–89% of accounts losing, and the UK FCA has cited roughly 80–82%. The academic record also shows that the profitability of simple technical rules in FX, which was real in the 1970s–80s, had largely decayed to around zero by the 1990s as markets grew more efficient. None of the strategies below is a money printer. They are frameworks whose edge, if any, is thin, regime-dependent, and erodes over time. The platform's value lies less in picking the "best" strategy than in disciplined regime detection, cost-aware backtesting, and risk control.

---

## At a glance

| Strategy | Core idea | Typical win rate | Reward:risk | Best regime | Worst regime |
|---|---|---|---|---|---|
| Trend-following | Ride sustained directional moves | ~30–45% | High (2:1 to 5:1) | Strong, persistent trends | Choppy/ranging; post-crisis |
| Mean reversion | Fade extremes back to a mean | ~60–80% | Low (≈1:1 to 1.5:1) | Range-bound, stable vol | Strong trends; vol spikes |
| Breakout/volatility | Enter as price escapes a range | ~30–40% | High (winners 3–10× losers) | Volatility expansion, session opens | Low-vol chop (fakeouts) |
| Carry trade | Earn interest-rate differential | High base rate | Steeply negative skew | Low vol, risk-on, wide rate gaps | Risk-off, vol spikes, unwinds |

The two pairings to internalize: trend-following and breakout are *positive-skew, low-win-rate* strategies — many small losses, occasional large wins. Mean reversion and carry are *negative-skew, high-win-rate* strategies — many small wins, occasional large losses. They fail in opposite conditions, which is the central reason diversifying across them (and detecting which regime you are in) matters more than optimizing any single one.

---

## 1. Trend-following / momentum

### How it works

Trend-following assumes that price moves which have begun tend to continue, and tries to capture the middle of sustained directional moves. The standard toolkit is moving-average systems (simple or exponential), the MACD, the ADX for trend-strength filtering, and momentum/rate-of-change measures. Moving-average crossovers provide the canonical signal: a faster MA crossing above a slower one is a long entry; crossing below is an exit or short. MACD — developed by Gerald Appel in the late 1970s — combines a fast and slow EMA plus a signal line, and is explicitly a *lagging* indicator: a trend must establish itself before MACD confirms it. The ADX (Wilder, 1978) measures trend *strength* but not direction, and is used to filter out the ranging markets where trend systems bleed.

### Entry/exit logic and typical parameters

- **EMA/SMA crossovers:** common fast systems use 9/21 EMA; the widely cited "golden cross" is the 50/200 SMA on daily charts (a very slow signal).
- **MACD:** standard parameters are 12/26/9 (12-period fast EMA, 26-period slow EMA, 9-period signal line). A faster variant is 5/35/5. Entry on MACD crossing above its signal line; the zero-line cross is a weaker, lower-lag confirmation.
- **ADX filter:** require ADX > 20 (trend exists) or > 40 (strong trend) before acting on a crossover; this is the single most effective whipsaw reducer.
- **Exits:** trailing stops are standard — frequently ATR-based, e.g. stop = price − (N × ATR) with N ≈ 1–3 and a 14-period ATR. Tight trails cut drawdown but exit early; loose trails capture bigger moves but give back more.

Sources: [Wikipedia — MACD](https://en.wikipedia.org/wiki/MACD); [Wikipedia — Moving average](https://en.wikipedia.org/wiki/Moving_average); [Wikipedia — Average Directional Index](https://en.wikipedia.org/wiki/Average_directional_index).

### Where it works vs. fails

It works in strong, persistent trends and through regime shifts that a moving-average filter can catch. It fails in ranging/choppy markets (ADX < 20), where lagging signals produce whipsaws — false breakouts followed by reversals that each cost a small loss. It also underperforms structurally after crises: Hutchinson & O'Brien (2014), using nearly a century of data, found trend-following returns are less than half their normal level in the roughly four-year window following the onset of a financial crisis, because time-series predictability weakens in those periods.

Sources: [QuantPedia — Asset Class Trend-Following](https://quantpedia.com/strategies/asset-class-trend-following); [Hutchinson & O'Brien (2014), SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2375733).

### Risk/reward profile

Low win rate (≈30–45%) offset by a high reward-to-risk ratio (roughly 2:1 to 5:1) — the strategy survives on a few large winners. Reported backtested Sharpe ratios for diversified, multi-asset trend systems land around 0.8–1.2, with maximum drawdowns commonly in the −20% to −40% range. Faber's (2006) 10-month moving-average tactical allocation famously delivered "equity-like returns with bond-like volatility and drawdowns" (documented max drawdown ≈ −29%). Position sizing must assume long losing streaks; 1–2% risk per trade is the norm to survive them.

Sources: [QuantPedia — Asset Class Trend-Following](https://quantpedia.com/strategies/asset-class-trend-following); [Faber (2006), SSRN](http://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461).

### Empirical evidence

Momentum is one of the most studied market anomalies: Jegadeesh & Titman (1993) documented cross-sectional momentum with excess returns near 1%/month. Time-series momentum (Moskowitz, Ooi & Pedersen, 2012) is robust across asset classes including currencies. But two cautions recur in the literature. Zakamulin (2012) shows reported trend performance often carries data-mining bias and ignores frictions; after realistic costs, out-of-sample results can be only marginally better than passive holding. And Nilsson (2012) frames the obvious dependency: trend-following only profits where markets exhibit autocorrelation or drift — in their absence it loses.

Sources: [Wikipedia — Momentum (finance)](https://en.wikipedia.org/wiki/Momentum_(finance)); [QuantPedia — Time Series Momentum](https://quantpedia.com/strategies/time-series-momentum-effect); [Zakamulin (2012), SSRN](http://papers.ssrn.com/sol3/papers.cfm?abstract_id=2242795).

### Key risks

Whipsaws in ranging markets; indicator lag (late entries, late exits); long, deep drawdowns and slow recoveries; suppressed returns for years after crises; sensitivity to volatility regime; and transaction-cost drag, which in choppy conditions can exceed the strategy's gross edge. Mitigations: ADX/volatility regime filters, multi-day signal confirmation, and multi-pair diversification.

---

## 2. Mean reversion

### How it works

Mean reversion assumes a currency pair oscillates around a central value and that large deviations tend to snap back. Traders "fade the extremes." The standard tools are the RSI (overbought/oversold momentum), Bollinger Bands (a moving average bracketed by standard-deviation bands), and the stochastic oscillator. In FX specifically there is a plausible structural basis: purchasing-power-parity anchors, central-bank intervention against runaway moves, and interest-rate expectations all create reversion pressure that is arguably stronger than in equities.

### Entry/exit logic and typical parameters

- **RSI:** 14-period standard, with 30/70 thresholds; a more aggressive variant uses a 10-period RSI with 20/80. A common rule is to wait for RSI to *cross back* through the threshold (e.g. drop below 20 then recross above it) rather than entering at the extreme itself.
- **Bollinger Bands:** 20-period SMA with ±2 standard deviations. Entry when price closes back inside after piercing a band; first target is the middle band (the 20-SMA). Some traders widen to ±2.5 or ±3σ for more selective entries.
- **Stochastic:** 14-period, 80/20 thresholds, entering on a %K/%D cross out of the extreme zone.
- **Exits:** target the mean (RSI ≈ 50, or the middle Bollinger band); stop placed beyond the swing extreme or ~1–2 ATR past it; often a time-based exit (close after N bars if reversion stalls).

Sources: [FOREX.com — three key indicators](https://www.forex.com/en-uk/trading-academy/courses/technical-analysis/uk-three-key-indicators/); [FXOpen — mean reversion strategies](https://fxopen.com/blog/en/mean-reversion-trading-strategies-and-indicators/); [ForexTester — mean reversion](https://forextester.com/blog/mean-reversion-trading/).

### Where it works vs. fails

It thrives in range-bound, sideways markets and stable-to-low volatility, and after news-driven overreactions that retrace. It fails — often catastrophically — in strong trends, where an oscillator can stay pinned at an extreme far longer than a fader can stay solvent. Regime shifts, liquidity crises, and volatility-regime changes (the "mean" itself moving) are the dangerous failure modes. The practical rule is to identify the regime on a higher timeframe before deploying mean reversion at all.

Sources: [TrendSpider — mean reversion strategies](https://trendspider.com/learning-center/mean-reversion-trading-strategies/); [Macro Ops — mastering mean reversion](https://macro-ops.com/mastering-mean-reversion/).

### Risk/reward profile

This is the mirror image of trend-following: high win rate (commonly cited at 60–80%) but a low reward-to-risk ratio (≈1:1 to 1.5:1), because the profit target (the mean) is close while the stop (beyond the extreme) is far. That asymmetry creates a *seductive but dangerous* equity curve — a string of small wins punctuated by occasional outsized losses. A high win rate without strict position sizing produces negative expectancy. Statistically-minded practitioners size by Z-score (smaller size at ±2σ, larger at ±3σ where reversion probability is higher), but this must be capped to survive a trend that doesn't revert.

Sources: [Macro Ops — mastering mean reversion](https://macro-ops.com/mastering-mean-reversion/); [QuantifiedStrategies — RSI trading](https://www.quantifiedstrategies.com/rsi-trading-strategy/).

### Empirical evidence and the stationarity requirement

The strategy's validity hinges on the price (or spread) series being *stationary* — mean-reverting rather than a random walk. The Augmented Dickey-Fuller (ADF) test is the standard check (p < 0.05 suggests stationarity); Z-scores and Ornstein-Uhlenbeck half-life estimates quantify deviation and expected reversion time. The crucial caveat for a platform: stationarity is not permanent. A pair that mean-reverted in-sample can stop doing so out-of-sample, which breaks the entire strategy logic. Rolling ADF / rolling-Z monitoring to detect when a pair has left its mean-reverting regime is close to mandatory.

Sources: [QuantVero — mean reversion strategy](https://www.quantvero.com/algo-trading/mean-reversion-trading-strategy/); [ACM — stationarity testing methods](https://dl.acm.org/doi/fullHtml/10.1145/3497701.3497736).

### Key risks

"Catching a falling knife" (fading a move that keeps going); trend-continuation/regime-shift losses; fat tails and slippage (stops filled far worse than intended in fast markets); and portfolio-level crowding, where many faders hold the same trade and liquidity evaporates together. Mitigations: higher-timeframe trend check, volume confirmation, time-based exits, position reduction when bands are widening, and rolling stationarity monitoring.

---

## 3. Breakout / volatility

### How it works

Breakout strategies enter as price escapes a consolidation range — through support/resistance, a Donchian channel boundary, a Bollinger "squeeze," or a session opening range. The premise is that a period of compressed volatility resolves into a directional expansion. ATR (Average True Range) is the connective tissue: it sizes stops and positions to current volatility so risk stays constant as conditions change. John Bollinger's observation that low-volatility periods tend to precede high-volatility ones is the logic behind the squeeze setup.

### Entry/exit logic and typical parameters

- **ATR:** 14-period standard. Stop multipliers typically 1.5× (day trades) to 2× (swing) up to 3× for volatile pairs like GBP/JPY. Position size = dollar risk ÷ (ATR × multiplier), so higher volatility automatically shrinks size.
- **Donchian channels:** 20-period is the classic breakout window; 20/55 combinations align with the Turtle system. Filtering breakouts by a longer moving average (e.g. only take upside breaks above a 100-period MA) materially improves signal quality.
- **Turtle Trading rules (the canonical mechanical breakout system):** System 1 — enter on a 20-day high, exit on a 10-day low; System 2 — enter on a 55-day high, exit on a 20-day low. Hard stop at 2N (2 × ATR). Position "unit" sized so a 1N move ≈ 1% of equity; pyramid up to 4 units, adding every 0.5N in favor; max loss per unit ≈ 2%.
- **Opening-range/London breakout:** define the range over the first 15–30 minutes of a session and trade the break. London handles roughly 35–40% of daily FX volume; one cited statistic is that EUR/USD breaks *both* sides of a 30-minute opening range about 65% of the time (i.e. fakeouts are common — direction filtering matters).

Sources: [QuantifiedStrategies — Turtle Trading](https://www.quantifiedstrategies.com/turtle-trading-strategy/); [Alchemy Markets — Turtle guide](https://alchemymarkets.com/education/strategies/turtle-trading-guide/); [LuxAlgo — ATR dynamic stops](https://www.luxalgo.com/blog/average-true-range-dynamic-stop-loss-levels/); [QuantifiedStrategies — London breakout](https://www.quantifiedstrategies.com/london-breakout-strategy/).

### Where it works vs. fails

It works during genuine volatility expansion — trend initiations, news, session opens with institutional order flow. It fails in low-volatility ranging markets, where "fakeouts" dominate: price pierces a level, triggers stops, and snaps back. Volume confirmation is the standard filter — breaks on rising volume are more likely real; breaks on thin volume are likely false.

Sources: [Equiti — fakeouts guide](https://www.equiti.com/sc-en/news/trading-ideas/how-to-identify-and-trade-fakeouts-a-complete-trader-guide/); [FTMO — breakouts and fakeouts](https://academy.ftmo.com/lesson/how-to-spot-breakouts-and-fakeouts/).

### Risk/reward profile

Low win rate (≈30–40%) with winners several times the size of losers — positive skew, like trend-following. ATR-normalized sizing is the defining risk feature: it holds dollar risk roughly constant across instruments and volatility regimes, taking larger size when quiet and smaller when wild. The Turtle 2N-stop / 1% unit framework is the textbook implementation.

Sources: [QuantifiedStrategies — position sizing in a Turtle system](https://www.quantifiedstrategies.com/position-sizing-in-a-turtle-trading-system/); [Collin Seow — volatility-based sizing](https://collinseow.com/volatility/).

### Empirical evidence — and decay

The Turtle experiment (Richard Dennis and William Eckhardt, mid-1980s) is the famous case: the group reportedly compounded around 80% per year and earned roughly $175M over about four years. **Treat that figure as a historical/promotional headline, not a forward expectation** — it comes from an exceptional trader/era and is widely repeated without independent audit. More sober: backtests of the raw Donchian/Turtle approach in modern markets show the edge has badly eroded (e.g. a Donchian 20/55 test across very large samples showing ~35% win rate, ~2.4:1 reward, but only a barely-positive profit factor; flat results in 1996–2009 tests). Opening-range breakout success rates are typically cited around 40–60% depending on filtering.

Sources: [QuantifiedStrategies — Richard Dennis](https://www.quantifiedstrategies.com/richard-dennis/); [turtletrader.com — The Bet](https://www.turtletrader.com/thebet/); [QuantifiedStrategies — Donchian channels](https://www.quantifiedstrategies.com/donchian-channel/); [LiteFinance — ORB strategy](https://www.litefinance.org/blog/for-beginners/trading-strategies/opening-range-breakout-strategy/).

### Key risks

False breakouts/whipsaws (the dominant failure mode); slippage, because breakouts happen on the fastest, thinnest tape of the day; and gaps/spikes around news. Mitigations: volume and trend filters, hard 2× ATR stops, waiting for a confirmed close beyond the level, and avoiding entries in the minutes immediately around high-impact releases.

---

## 4. Carry trade

### How it works

The carry trade borrows a low-interest-rate "funding" currency (classically the yen) and invests in a higher-yielding "target" currency, earning the interest-rate differential ("carry"), collected daily as broker rollover/swap. It is fundamentally different from the three technical strategies above: the edge is an interest differential, not a price pattern. Its theoretical justification is the empirical *failure* of uncovered interest-rate parity (UIP) — the "forward premium puzzle." UIP predicts high-yield currencies should depreciate enough to erase the rate advantage; in practice they often don't (and sometimes appreciate), leaving a persistent return that carry traders harvest.

### Mechanics and classic examples

Rollover/swap is credited or debited at the daily rollover (≈17:00 NY time), with a triple charge on Wednesdays to cover the weekend. Classic funding currencies: JPY (BOJ near-zero rates for two decades, raised to 0.25% in July 2024) and CHF. Classic targets: AUD, NZD, and higher-yield emerging-market currencies (MXN, BRL, ZAR, TRY). Implementation ranges from OTC FX forwards/swaps (the bulk, in the trillions of notional) to on-balance-sheet yen borrowing, offshore SPVs, and retail margin accounts.

Sources: [AMRO — carry trade analytical note (Dec 2024)](https://amro-asia.org/wp-content/uploads/2024/12/20241219-Analytical_Note_Carry_Trade.pdf); [BIS Bulletin No. 90 (Aug 2024)](https://www.bis.org/publ/bisbull90.pdf); [FP Markets — FX swap rates](https://www.fpmarkets.com/blog/introduction-to-forex-swap-rates/).

### Where it works vs. fails

It works in low-volatility, risk-on environments with wide, stable rate differentials and a stable-to-weakening funding currency. It fails when volatility spikes and sentiment turns risk-off: carry has implicit *short-volatility* exposure, so a vol shock forces margin calls and a self-reinforcing unwind as everyone buys back the funding currency at once.

Sources: [BIS Bulletin No. 90](https://www.bis.org/publ/bisbull90.pdf); [Brunnermeier, Nagel & Pedersen (2008), NBER](https://www.nber.org/papers/w14473).

### Risk/reward profile — "picking up nickels in front of a steamroller"

Steady small gains, then rare violent losses: carry returns are significantly *negatively skewed*. Investors are, in effect, paid for bearing crash risk, not just for the interest spread. Single-pair carry (especially JPY vs. an emerging market) carries the worst skew; a diversified, multi-pair carry basket has historically shown markedly better risk-adjusted characteristics (higher Sharpe, much less skew), suggesting the return is partly a diversifiable, partly a systematic global-risk premium.

Sources: [Daniel, Hodrick & Lu (2017), Critical Finance Review](https://business.columbia.edu/sites/default/files-efs/pubfiles/6378/Daniel.Hodrick.Lu.Carry%20Trade.Critical%20Finance%20Review.2017.pdf); [Brunnermeier, Nagel & Pedersen (2008)](https://www.nber.org/papers/w14473).

### Empirical evidence

Long-run currency carry delivered roughly 2.4%/year excess return with a Sharpe near 0.26 over 1901–2012 — modest. Shorter modern samples and multi-asset-class carry baskets show higher Sharpes (0.6 up to ~1.2 diversified). Lustig, Roussanov & Verdelhan (2011) identify a "slope" risk factor that prices the cross-section of currency returns and correlates with global equity volatility — i.e. carry is compensation for a systematic risk. The UIP/forward-premium anomaly that underpins all of this is one of the most durable puzzles in international finance.

Sources: [Lustig, Roussanov & Verdelhan (2011), RFS](https://www3.nd.edu/~nmark/GradMacroFinance/LustigRoussanovVerdelhan_RFS_2011.pdf); [Koijen, Moskowitz, Pedersen & Vrugt — "Carry"](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2014/06/Carry.pdf).

### Key risks — illustrated by August 2024

The August 2024 yen-carry unwind is the textbook recent case. The BOJ hiked on 31 July 2024; a weak US jobs print on 2 August narrowed the differential; by 5 August the Japanese TOPIX fell ~12% (worst since 1987), the yen had surged ~5–6% against the dollar in days, the VIX spiked to near-pandemic levels, and risk assets (including crypto, down ~20%) sold off globally before a fast recovery. Estimated carry positioning going in was on the order of ¥40 trillion (~$250B), with estimates ranging widely. The broader risks: currency-crash risk, leverage, crowded-trade cascades, and central-bank action; note that sterilized intervention alone cannot stop a carry trade — only higher domestic rates or capital controls do.

Sources: [BIS Bulletin No. 90](https://www.bis.org/publ/bisbull90.pdf); [World Economic Forum — carry trades explainer (Aug 2024)](https://www.weforum.org/stories/2024/08/explainer-carry-trades-and-how-they-impact-global-markets/).

---

## 5. Cross-cutting: backtesting, risk, and realistic expectations

These apply to every strategy above and are where automated systems most often fail silently.

### Backtesting pitfalls (all bias toward over-optimism)

- **Overfitting/curve-fitting:** a strategy needing many tuned parameters is usually fitting noise. Keep rule-sets simple; check that performance degrades gracefully as parameters vary.
- **Look-ahead bias:** using data not yet available at decision time. Use point-in-time data; if a strategy survives an extra one-period lag on every input, it's likelier real.
- **Survivorship bias:** testing only instruments that still exist overstates returns (~0.9%/year in one mutual-fund study).
- **Data-snooping / multiple testing:** Harvey, Liu & Zhu (2016) argue the usual t-stat > 2.0 bar is far too low given how many strategies get tested; they suggest ~3.0 for new factors. Reserve genuine out-of-sample data; use walk-forward analysis.
- **Transaction costs/slippage:** frequent, small-edge strategies are exquisitely sensitive to costs. Model spread, market impact, and slippage; stress at 1.5–2× your best cost estimate. Backtested Sharpe ~2.0 commonly becomes ~1.0–1.4 live, largely from unmodeled costs.

Sources: [HedgeFundAlpha — backtesting mistakes](https://hedgefundalpha.com/education/backtesting-mistakes-kill-quant-strategies-guide/); [Harvey, Liu & Zhu (2016), RFS](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824); [Interactive Brokers — walk-forward analysis](https://www.interactivebrokers.com/campus/ibkr-quant-news/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis/).

### The sobering base rate

Regulatory disclosures: ESMA-jurisdiction brokers report roughly 74–89% of retail CFD/forex accounts lose money; the UK FCA has cited ~80–82%. The FCA attributes this partly to the structural asymmetry of the leveraged CFD model.

Sources: [ESMA — CFD product intervention](https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors); [FCA — CFD investor warning](https://www.fca.org.uk/news/press-releases/fca-warns-investors-cfds-risk-losing-out-protections).

### Metrics to track

Sharpe (risk-adjusted return; >1 good, >2 very good, >3 rare/excellent), Sortino (downside-only, often more meaningful), maximum drawdown (target < ~20%), profit factor (> 1), and Calmar (return ÷ max drawdown). Judge by risk-adjusted return and drawdown, never by win rate alone — recall that mean reversion and carry post high win rates while hiding tail risk.

Sources: [QuantifiedStrategies — trading performance metrics](https://www.quantifiedstrategies.com/trading-performance/); [QuantStart — Sharpe ratio](https://www.quantstart.com/articles/Sharpe-Ratio-for-Algorithmic-Trading-Performance-Measurement/).

### Risk management essentials

Risk a fixed fraction per trade (commonly 1–2%; beginners 0.5–1%), with position size = risk amount ÷ (stop in pips × pip value). Treat leverage as a derived quantity, not a target. Watch correlation: long EUR/USD and GBP/USD simultaneously is a doubled USD bet. The Kelly criterion gives a theoretical optimum but full Kelly is too volatile in practice — fractional Kelly (e.g. ¼) is standard, and it needs a stable estimate from 50–100+ trades.

Sources: [TradingwithRayner — forex risk management](https://www.tradingwithrayner.com/forex-risk-management/); [zForex — position sizing](https://zforex.com/blog/forex/position-sizing-in-forex-balancing-leverage-and-risk/).

### Does technical analysis even work in FX?

The honest academic answer is "it used to, less so now." Park & Irwin's (2007) review found most modern studies showed technical rules were profitable in FX, at least into the early 1990s. But profitability has decayed: dynamically selecting the best moving-average rule across developed currencies gave high returns in the 1970s, moderate in the 1980s, and roughly zero by the 1990s. This is the Adaptive Markets Hypothesis in action — as a signal gets crowded, its edge erodes. Practical implication: any edge you find will likely decay (forex predictive signals are estimated to lose ~5–10% of effectiveness per year), so continuous monitoring and re-validation must be built into the platform.

Sources: [Park & Irwin (2007), Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-6419.2007.00519.x); [St. Louis Fed — TA in the FX market](https://files.stlouisfed.org/files/htdocs/wp/2011/2011-001.pdf).

---

## 6. Implications for the AI Trading Intelligence Platform

Mapping this to the platform's existing design and rules (`CLAUDE.md`):

1. **Regime detection is the highest-leverage feature.** The strategies fail in opposite conditions. An ADX/volatility classifier that gates which strategy is allowed to trade (trend/breakout when ADX is high and volatility expanding; mean reversion only in confirmed ranges; carry only when vol is low and rate gaps wide) will likely matter more than any single signal's parameters.

2. **ATR everywhere.** The platform's glossary already centers ATR. Use it for volatility-normalized stops (2–3×) and position sizing across all four strategies so dollar-risk stays constant. This is exactly the Turtle "N" framework.

3. **The risk engine and journaling rules are the right instinct.** Because mean reversion and carry hide negative skew behind high win rates, the "risk engine before any trade" and "journal every signal with reasoning" rules are not bureaucracy — they are the specific controls that catch the failure modes documented here. Enforce 1–2% risk per trade and correlation caps programmatically, not as guidance.

4. **Backtest honestly, then paper trade — as the rules already require.** Bake in walk-forward analysis, out-of-sample holdout, multiple-testing skepticism (t-stat ~3), and cost stress at 1.5–2× estimate. Expect live Sharpe well below backtest.

5. **Plan for decay.** Build rolling re-validation (rolling ADF for mean-reversion pairs; rolling performance/Sharpe monitoring for all) and alerts when a strategy leaves the regime it was validated in.

6. **Prefer diversification over a single "best" strategy.** Both the trend-following and carry literature show diversified, multi-pair/multi-strategy baskets have better risk-adjusted profiles and less catastrophic skew than concentrated bets.

---

## Caveats on the evidence

A few figures in this report should be read with care. The Turtle "~80% annual return" is a celebrated historical/promotional figure from one trader and era, not an audited forward expectation; modern Donchian/Turtle backtests are far weaker. Reported win rates and Sharpe ranges vary widely by source, sample, costs, and pair — treat them as orders of magnitude, not precise constants. Several educational sources (broker blogs, strategy sites) are inherently promotional; the academic and central-bank sources (NBER, BIS, the Fed, RFS, ESMA, FCA) are the most reliable and were weighted accordingly. Finally, none of this is investment advice — it is a technical/strategic survey to inform platform design.

---

## Consolidated sources

**Academic & central bank (highest reliability)**
- Brunnermeier, Nagel & Pedersen (2008), Carry Trades and Currency Crashes — https://www.nber.org/papers/w14473
- Lustig, Roussanov & Verdelhan (2011), Common Risk Factors in Currency Markets — https://www3.nd.edu/~nmark/GradMacroFinance/LustigRoussanovVerdelhan_RFS_2011.pdf
- Daniel, Hodrick & Lu (2017), The Carry Trade: Risks and Drawdowns — https://business.columbia.edu/sites/default/files-efs/pubfiles/6378/Daniel.Hodrick.Lu.Carry%20Trade.Critical%20Finance%20Review.2017.pdf
- Koijen, Moskowitz, Pedersen & Vrugt, Carry — https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2014/06/Carry.pdf
- Harvey, Liu & Zhu (2016), …and the Cross-Section of Expected Returns — https://academic.oup.com/rfs/article-abstract/29/1/5/1843824
- Park & Irwin (2007), What Do We Know About the Profitability of Technical Analysis? — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-6419.2007.00519.x
- Hutchinson & O'Brien (2014), Is This Time Different? Trend-Following and Financial Crises — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2375733
- St. Louis Fed (2011), Technical Analysis in the Foreign Exchange Market — https://files.stlouisfed.org/files/htdocs/wp/2011/2011-001.pdf
- BIS Bulletin No. 90 (Aug 2024), The market turbulence and carry trade unwind of August 2024 — https://www.bis.org/publ/bisbull90.pdf
- AMRO (Dec 2024), Understanding Currency Carry Trades — https://amro-asia.org/wp-content/uploads/2024/12/20241219-Analytical_Note_Carry_Trade.pdf
- ESMA, CFD product intervention — https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors
- FCA, CFD investor warning — https://www.fca.org.uk/news/press-releases/fca-warns-investors-cfds-risk-losing-out-protections

**Reference & educational**
- Wikipedia — MACD — https://en.wikipedia.org/wiki/MACD
- Wikipedia — Moving average — https://en.wikipedia.org/wiki/Moving_average
- Wikipedia — Average Directional Index — https://en.wikipedia.org/wiki/Average_directional_index
- Wikipedia — Momentum (finance) — https://en.wikipedia.org/wiki/Momentum_(finance)
- QuantPedia — Asset Class Trend-Following — https://quantpedia.com/strategies/asset-class-trend-following
- QuantPedia — Time Series Momentum — https://quantpedia.com/strategies/time-series-momentum-effect
- Faber (2006), A Quantitative Approach to Tactical Asset Allocation — http://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461
- Zakamulin (2012) — http://papers.ssrn.com/sol3/papers.cfm?abstract_id=2242795
- FOREX.com — three key indicators — https://www.forex.com/en-uk/trading-academy/courses/technical-analysis/uk-three-key-indicators/
- FXOpen — mean reversion strategies — https://fxopen.com/blog/en/mean-reversion-trading-strategies-and-indicators/
- ForexTester — mean reversion — https://forextester.com/blog/mean-reversion-trading/
- Macro Ops — mastering mean reversion — https://macro-ops.com/mastering-mean-reversion/
- TrendSpider — mean reversion strategies — https://trendspider.com/learning-center/mean-reversion-trading-strategies/
- QuantVero — mean reversion strategy — https://www.quantvero.com/algo-trading/mean-reversion-trading-strategy/
- QuantifiedStrategies — Turtle Trading — https://www.quantifiedstrategies.com/turtle-trading-strategy/
- QuantifiedStrategies — Richard Dennis — https://www.quantifiedstrategies.com/richard-dennis/
- QuantifiedStrategies — Donchian channels — https://www.quantifiedstrategies.com/donchian-channel/
- QuantifiedStrategies — position sizing in a Turtle system — https://www.quantifiedstrategies.com/position-sizing-in-a-turtle-trading-system/
- QuantifiedStrategies — London breakout — https://www.quantifiedstrategies.com/london-breakout-strategy/
- QuantifiedStrategies — trading performance metrics — https://www.quantifiedstrategies.com/trading-performance/
- Alchemy Markets — Turtle guide — https://alchemymarkets.com/education/strategies/turtle-trading-guide/
- turtletrader.com — The Bet — https://www.turtletrader.com/thebet/
- LuxAlgo — ATR dynamic stops — https://www.luxalgo.com/blog/average-true-range-dynamic-stop-loss-levels/
- LiteFinance — ORB strategy — https://www.litefinance.org/blog/for-beginners/trading-strategies/opening-range-breakout-strategy/
- Equiti — fakeouts guide — https://www.equiti.com/sc-en/news/trading-ideas/how-to-identify-and-trade-fakeouts-a-complete-trader-guide/
- FTMO — breakouts and fakeouts — https://academy.ftmo.com/lesson/how-to-spot-breakouts-and-fakeouts/
- FP Markets — FX swap rates — https://www.fpmarkets.com/blog/introduction-to-forex-swap-rates/
- World Economic Forum — carry trades explainer — https://www.weforum.org/stories/2024/08/explainer-carry-trades-and-how-they-impact-global-markets/
- HedgeFundAlpha — backtesting mistakes — https://hedgefundalpha.com/education/backtesting-mistakes-kill-quant-strategies-guide/
- Interactive Brokers — walk-forward analysis — https://www.interactivebrokers.com/campus/ibkr-quant-news/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis/
- QuantStart — Sharpe ratio — https://www.quantstart.com/articles/Sharpe-Ratio-for-Algorithmic-Trading-Performance-Measurement/
- TradingwithRayner — forex risk management — https://www.tradingwithrayner.com/forex-risk-management/
- zForex — position sizing — https://zforex.com/blog/forex/position-sizing-in-forex-balancing-leverage-and-risk/
