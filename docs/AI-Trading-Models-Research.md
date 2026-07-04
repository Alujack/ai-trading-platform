# Open-Source AI Models for Trading — Deep Research Report

**Scope:** Self-hostable, open-source AI/ML models for trading, with emphasis on Hugging Face availability. Covers four categories — time-series forecasting, news/sentiment, finance LLM agents, and reinforcement-learning frameworks — with a forex (FX) focus.
**Date:** June 26, 2026
**Prepared for:** AI Trading Intelligence Platform (`services/ai` FastAPI service)

---

## TL;DR — the honest answer to "is there a powerful AI model for trading?"

Yes, there are many capable open-source models on Hugging Face, and several are genuinely useful as **components**. But the rigorous evidence is blunt: **no open model reliably predicts forex price direction better than a coin flip after spread/costs at short horizons.** A 2026 controlled study of 918 experiments across 9 deep-learning architectures (including on forex) found mean directional accuracy of **50.08%** — statistically indistinguishable from chance.

So the realistic framing for your platform: use these models for **feature generation and reasoning** (volatility forecasting, news/sentiment scoring, regime filtering, journaled reasoning, risk sizing) — not as a standalone "predict the next candle" oracle. This aligns with the rules already in your `CLAUDE.md`: risk engine before execution, backtest everything, paper-trade first, journal every signal.

The single most relevant finding: in *"Re(Visiting) Time Series Foundation Models in Finance"* (Nov 2025), off-the-shelf TimesFM and Chronos **underperformed plain gradient boosting (CatBoost/LightGBM)** on financial returns — TimesFM-500M posted out-of-sample R² of **-2.80%** and sub-50% directional accuracy. Fine-tuning didn't close the gap; only models pretrained from scratch on financial data helped.

---

## 1. Time-Series Forecasting Foundation Models

These predict future values from price history. All are zero-shot capable (except PatchTST, which you train). **Key caveat for FX:** they forecast *levels/volatility* far better than *return direction* — and direction is what trading needs.

| Model | HF repo | License | Size | Covariates | Notes |
|---|---|---|---|---|---|
| **Chronos-2** | `amazon/chronos-2` | Apache-2.0 | 120M | **Yes (native)** | **Best all-round.** Cleanest API, takes covariates (feed ATR/RSI/EMA), production-friendly. |
| **Chronos-Bolt** | `amazon/chronos-bolt-base` | Apache-2.0 | 9–205M | Limited | ~250x faster than original Chronos. Cheap zero-shot baseline. |
| **TimesFM 2.5** | `google/timesfm-2.5-200m-pytorch` | Apache-2.0 | 200M | No | Simple univariate baseline; quantile output. v2.0 = `google/timesfm-2.0-500m-pytorch`. |
| **Granite TTM** | `ibm-granite/granite-timeseries-ttm-r2` | Apache-2.0 | ~1M+ | **Yes** | **Tiny, runs on CPU.** MLP-Mixer, not transformer. One of few TSFMs whose zero-shot beat naive baselines on volatility/spread tasks. |
| **TOTO** | `Datadog/Toto-Open-Base-1.0` | Apache-2.0 | 151M–2.5B | **Yes** | Built for spiky, non-stationary data (server metrics); probabilistic. Worth testing on noisy FX. |
| **Time-MoE** | `Maple728/TimeMoE-200M` | Apache-2.0 | 50M–2.4B | No | Sparse mixture-of-experts; scalable univariate. |
| **Lag-Llama** | `time-series-foundation-models/Lag-Llama` | Apache-2.0 | small | lags | Older; probabilistic; usually outperformed by Chronos/TimesFM. |
| **PatchTST** | in HF `transformers` (`PatchTSTForPrediction`) | Apache-2.0 (code) | configurable | channels | **Not a foundation model** — you train it on your own candles. Good for full control. |
| **Moirai / Moirai-MoE** | `Salesforce/moirai-2.0-R-small` | **CC-BY-NC-4.0** | 117M–935M | Yes | Multivariate + covariates, but **NON-COMMERCIAL license — avoid in production.** |
| **TimeGPT (Nixtla)** | API only | model closed | — | Yes | **Not self-hostable for free.** Only the SDK is open; weights are commercial/API. |

**Recommendation:** Start with **Chronos-2** (covariate-aware, Apache-2.0, cleanest pandas API) and **Granite TTM** (tiny, CPU, good for volatility/ATR targets). Use **TimesFM 2.x** as a simple baseline. Avoid Moirai (non-commercial) and TimeGPT (not free to self-host).

> **FX reality:** Treat any TSFM as *one feature among many*, fine-tuned on your data. Raw zero-shot return-direction prediction is documented to perform at or below chance. There's also a finance-specific foundation model, **FinCast** (arXiv 2508.19609, CIKM 2025), tested on forex that claims ~20% lower error than TimesFM/Chronos — but its public weights/license are unverified.

---

## 2. Financial News / Sentiment Models

These convert news/headlines into sentiment scores. All self-hostable via `transformers`. **Big caveat: nearly all were trained on equity/earnings text, not forex/macro news.** Forex sentiment is *relational* (EUR up = USD down), which single-label classifiers lose.

| Model | HF repo | License | Size | Notes |
|---|---|---|---|---|
| **FinBERT (Prosus)** | `ProsusAI/finbert` | Apache-2.0 (GitHub) | ~110M | The de-facto default. Clean pos/neg/neutral labels, huge adoption. Best starting baseline. |
| **distilRoBERTa financial news** | `mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis` | Apache-2.0 | ~82M | **Fastest/smallest** — best for high-volume headline throughput. |
| **FinBERT-tone** | `yiyanghkust/finbert-tone` | ⚠️ unspecified | ~110M | Analyst-report tone. Labels need ID remapping. License unclear — verify before commercial use. |
| **FinancialBERT** | `ahmedrachid/FinancialBERT-Sentiment-Analysis` | ⚠️ unspecified | ~340M | High in-domain F1 (optimistic). License gap. |
| **FinTwitBERT** | `StephanAkkerman/FinTwitBERT-sentiment` | MIT (verify) | ~110M | Bearish/neutral/bullish on financial tweets — tradeable framing for social feeds. |
| **FinGPT sentiment** | `FinGPT/fingpt-sentiment_llama2-13b_lora` | MIT adapter (Llama-2 base license governs) | 13B base + LoRA | Generative sentiment; best-in-class benchmark scores but GPU-heavy, higher latency. |

**Recommendation:** Use **`ProsusAI/finbert`** or **`mrm8488/distilroberta-...`** (Apache-2.0, clean labels, fast) as a baseline headline-sentiment feature. **Plan to fine-tune on forex/macro-labelled news** before trusting it as a signal — and model sentiment per-currency-pair, not per-headline.

> **FX reality:** Off-the-shelf, these add little FX value. Where sentiment plausibly helps is **central-bank communication tone** (which measurably affects rates and EUR/USD volatility) and *domain-specific* macro news — but only as one input among many, never standalone.

---

## 3. Finance LLM Agents & Reasoning Models

Instruction-tuned finance LLMs and multi-agent trading frameworks. **None have credible public evidence of producing profitable live signals**, and reported backtests are heavily contaminated by lookahead/data-leakage bias.

| Model | HF repo | License | Size | Notes |
|---|---|---|---|---|
| **Fin-R1** | `SUFE-AIFLM-Lab/Fin-R1` | **Apache-2.0** | 7B (Qwen2.5) | **Best license + most production-friendly.** Financial *reasoning* (CoT), compliance checks, QA. vLLM-ready. Not a price predictor; Chinese-finance skew. |
| **FinMA / PIXIU** | `ChanceFocus/finma-7b-full` | MIT (gated; Llama-v1 base) | 7B | Solid on finance NLP benchmarks; weak as directional predictor. Easy OpenAI-compatible serving. |
| **FinGPT** | `FinGPT/fingpt-mt_llama3-8b_lora` etc. | base-license bound | 7–20B + LoRA | Sentiment adapters are the genuinely useful part; the "forecaster" is a research demo with documented bullish bias. |
| **Palmyra-Fin** | `Writer/Palmyra-Fin-70B-32K` | ⚠️ Writer Open Model (non-commercial) | ~72B | Strong on CFA-level analysis/documents; **commercial use requires Writer license.** |
| **InvestLM** | `yixuantt/InvestLM-mistral-AWQ` | ⚠️ "test only, not for sharing" | ~47B MoE | Good investment Q&A; effectively research-only/non-redistributable. |
| **FinLlama-3** | `roma2025/FinLlama-3-8B` | Llama-3 Community | 8B | Practical sentiment feature generator. |
| **TradingAgents** | GitHub `TauricResearch/TradingAgents` | ⚠️ verify | framework | Multi-agent (analyst→debate→trader→risk) orchestration over any LLM; runs on local Ollama/vLLM. Equity-only. |

**Recommendation:** For your stack, **Fin-R1** (Apache-2.0, 7B, vLLM) is the standout — ideal for generating the **journaled reasoning** your `CLAUDE.md` requires and for compliance/sanity checks behind the risk engine. Use FinMA/FinLlama as small NLP feature generators. Treat the agent frameworks as paper-trade experiments only.

> **FX reality:** There is **no FX-tuned LLM** and essentially no FX evaluation in this category — all built on US/Chinese equities, earnings calls, SEC filings, CFA material. The *"Profit Mirage"* study (arXiv 2510.07920) found LLM trading agents lose ~50% of reported performance when tested beyond their training cutoff.

---

## 4. Reinforcement-Learning Trading Frameworks

These are **code libraries, not pre-trained models** — almost none ship usable weights on Hugging Face. The value is the training/backtesting pipeline.

| Framework | Repo | License | FX support | Notes |
|---|---|---|---|---|
| **gym-anytrading** | `AminHP/gym-anytrading` | MIT | **Native `ForexEnv`** + EUR/USD sample data | **Best native FX starting point.** Teaching-grade (no spread/slippage realism). |
| **gym-mtsim** | `AminHP/gym-mtsim` | MIT (verify) | **MetaTrader 5 sim**, FX/stocks/crypto | Step up from gym-anytrading for MT5 realism. |
| **Stable-Baselines3** | `DLR-RM/stable-baselines3` | MIT | agnostic | **Best inference path** — `model.predict(obs)`, easy to serve in FastAPI. Pair with gym-anytrading. PPO/A2C/SAC/TD3/DQN. |
| **FinRL** | `AI4Finance-Foundation/FinRL` | MIT (verify) | not first-class | Full train/test/trade pipeline; A2C/DDPG/PPO/TD3/SAC. Stock/crypto focus — FX needs custom data env. Alpaca paper trading. |
| **FinRL-Meta** | `AI4Finance-Foundation/FinRL-Meta` | MIT (verify) | not first-class | Data + environments + benchmarks layer. |
| **FinRL-DeepSeek** | `benstaf/FinRL_DeepSeek` (+ HF weights `benstaf/Trading_agents`) | — | No (Nasdaq-100) | **Rare case with HF weights.** Adds LLM news signals into risk-sensitive CPPO. |
| **ElegantRL** | `AI4Finance-Foundation/ElegantRL` | Apache-2.0 | agnostic | High-performance parallel RL engine; general-purpose. |
| **TensorTrade** | `tensortrade-org/tensortrade` | Apache-2.0 | agnostic | Composable; asset-agnostic. Check maintenance status. |

**Recommendation:** For FX prototyping: **gym-anytrading (or gym-mtsim for MT5) + Stable-Baselines3** — cleanest, MIT-licensed, easiest to serve. Use FinRL/FinRL-Meta if you want the fuller pipeline (but expect to build FX data processing yourself).

> **FX reality:** RL trading agents mostly **do not work reliably live.** Documented failure modes: non-stationarity (an agent tuned for one regime "often fails catastrophically" in another — acute for FX), overfitting, sim-to-real gap, and transaction costs (deep-RL agents were profitable only up to ~25 bps cost; above realistic retail spreads the edge vanished). Use RL as a research/signal experiment behind your risk engine, never as an unsupervised live executor.

---

## 5. The Skeptic's Section — does any of this work for live forex?

The evidence is unusually candid, and it matters for how you build:

- **Coin-flip ceiling:** 918-experiment study across 9 architectures (incl. forex) → **50.08% mean directional accuracy.** (arXiv 2603.16886)
- **Meese-Rogoff puzzle (1983, still standing 40+ years):** exchange-rate models can't beat a random walk out-of-sample, especially at short horizons. Central banks (ECB, RBA) treat this seriously.
- **Low signal-to-noise is structural:** AQR argues markets *actively destroy* exploitable signal through competition — this is why finance is harder for ML than image recognition.
- **TSFMs fail on returns:** TimesFM/Chronos underperform gradient boosting; long-short TimesFM portfolio ≈ **-1.47% annualized** vs ~46.5% for best CatBoost. (arXiv 2511.18578)
- **LLM backtests are contaminated:** Glasserman & Lin show look-ahead + "distraction" bias when training data overlaps the backtest window. Lopez-Lira finds GPT sentiment predicts *equity* returns — but the edge decays as adoption rises (arbitraged away).
- **Backtest overfitting is easy:** with 5 years of daily data, trying ~45 strategy variants yields a Sharpe ≥ 1.0 **by pure chance.** Use purged/combinatorial CV and the Deflated Sharpe Ratio.
- **What practitioners say works:** Ernie Chan — using ML to *find alpha* "has not been fruitful"; the productive use is ML as a **risk filter and position sizer** on an economically-motivated strategy, deciding when *not* to trade.

**What plausibly helps (with discipline):** ML as a risk/regime filter and position sizer; volatility forecasting (TSFMs do okay here); central-bank/macro text tone; domain-specific sentiment combined with other signals; rigorous validation with realistic costs.

**Be very skeptical of:** any standalone "predicts direction" claim; high backtest accuracy without cost-adjusted P&L; short backtests with huge returns; LLM/sentiment results inside the training window; complex nets beating simple baselines; zero-shot TSFMs on returns.

---

## Suggested architecture for your platform

Given your FastAPI `services/ai` and FX focus, a pragmatic, license-clean stack:

1. **Volatility / level forecasting:** Chronos-2 or Granite TTM (Apache-2.0, covariate-aware) → feed engineered indicators (ATR, RSI, EMA) as covariates. Use for position sizing & regime detection, not direction.
2. **News/sentiment feature:** `ProsusAI/finbert` or distilRoBERTa baseline → **fine-tune on forex/macro news**, score per currency pair. Prioritize central-bank communications.
3. **Reasoning & journaling:** Fin-R1 (Apache-2.0, vLLM) → generate the journaled reasoning your rules require, plus compliance/sanity checks before the risk engine.
4. **Strategy research:** gym-anytrading/gym-mtsim + Stable-Baselines3 (MIT) → RL experiments, always backtested with realistic FX spreads and paper-traded first.
5. **Validation discipline:** purged/combinatorial CV, Deflated Sharpe Ratio, walk-forward, realistic transaction costs — baked into your backtest gate.

All four model categories are best treated as **signal inputs into a disciplined system**, never as a standalone trade oracle — which is exactly the posture your `CLAUDE.md` rules already encode.

---

## License watch-list (verify before commercial deployment)

- **Avoid in production:** Moirai (CC-BY-NC-4.0), Palmyra-Fin (non-commercial), InvestLM ("test only, not for sharing"), TimeGPT (closed weights).
- **License unverified on HF card:** FinBERT-tone, FinancialBERT, nickmuchi fine-tunes — treat as non-commercial-safe until confirmed.
- **Llama-based models** (FinGPT, FinMA, FinLlama): the **base model license** (Meta Llama Community License) governs deployment, not the adapter's MIT tag.
- **Cleanly commercial (Apache-2.0/MIT):** Chronos family, TimesFM, Granite TTM, TOTO, Time-MoE, Lag-Llama, Fin-R1, ProsusAI/finbert, Stable-Baselines3, gym-anytrading, FinRL, ElegantRL, TensorTrade — *still verify the LICENSE file for the FinRL family.*

---

## Sources

**Time-series models:**
- https://huggingface.co/amazon/chronos-2 · https://huggingface.co/amazon/chronos-bolt-base · https://github.com/amazon-science/chronos-forecasting
- https://huggingface.co/google/timesfm-2.5-200m-pytorch · https://huggingface.co/google/timesfm-2.0-500m-pytorch
- https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2 · https://huggingface.co/Datadog/Toto-Open-Base-1.0
- https://huggingface.co/Maple728/TimeMoE-200M · https://huggingface.co/time-series-foundation-models/Lag-Llama
- https://huggingface.co/docs/transformers/en/model_doc/patchtst · https://huggingface.co/Salesforce/moirai-2.0-R-small
- https://github.com/Nixtla/nixtla · https://arxiv.org/abs/2508.19609 (FinCast)

**Sentiment models:**
- https://huggingface.co/ProsusAI/finbert · https://github.com/ProsusAI/finBERT · https://arxiv.org/abs/1908.10063
- https://huggingface.co/mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis
- https://huggingface.co/yiyanghkust/finbert-tone · https://huggingface.co/ahmedrachid/FinancialBERT-Sentiment-Analysis
- https://huggingface.co/StephanAkkerman/FinTwitBERT-sentiment · https://huggingface.co/FinGPT/fingpt-sentiment_llama2-13b_lora
- https://dl.acm.org/doi/fullHtml/10.1145/3572647.3572667 (FinBERT for FX) · https://www.sciencedirect.com/science/article/abs/pii/S1568494626000049

**Finance LLMs / agents:**
- https://huggingface.co/SUFE-AIFLM-Lab/Fin-R1 · https://arxiv.org/abs/2503.16252
- https://huggingface.co/ChanceFocus/finma-7b-full · https://github.com/chancefocus/PIXIU · https://arxiv.org/abs/2306.05443
- https://huggingface.co/FinGPT · https://github.com/AI4Finance-Foundation/FinGPT · https://arxiv.org/html/2507.08015v1
- https://huggingface.co/Writer/Palmyra-Fin-70B-32K · https://huggingface.co/yixuantt/InvestLM-mistral-AWQ
- https://huggingface.co/roma2025/FinLlama-3-8B · https://github.com/TauricResearch/TradingAgents · https://arxiv.org/html/2412.20138v1
- https://arxiv.org/html/2510.07920v1 (Profit Mirage)

**RL frameworks:**
- https://github.com/AI4Finance-Foundation/FinRL · https://github.com/AI4Finance-Foundation/FinRL-Meta
- https://github.com/benstaf/FinRL_DeepSeek · https://huggingface.co/benstaf/Trading_agents · https://arxiv.org/abs/2502.07393
- https://github.com/AI4Finance-Foundation/ElegantRL · https://github.com/tensortrade-org/tensortrade
- https://github.com/DLR-RM/stable-baselines3 · https://github.com/AminHP/gym-anytrading · https://github.com/AminHP/gym-mtsim
- https://dl.acm.org/doi/10.1145/3533271.3561780 (FX non-stationarity)

**FX efficacy / skeptic evidence:**
- https://arxiv.org/abs/2603.16886 (918-experiment coin-flip study) · https://arxiv.org/abs/2511.18578 (TSFMs in finance)
- https://arxiv.org/abs/2304.07619 (Lopez-Lira) · https://arxiv.org/abs/2309.17322 (Glasserman & Lin lookahead bias)
- https://cepr.org/voxeu/columns/can-we-predict-exchange-rates-economic-evidence-against-random-walk-model (Meese-Rogoff)
- https://www.aqr.com/Learning-Center/Machine-Learning/Machine-Learning-Why-Finance-Is-Different
- https://www.davidhbailey.com/dhbpapers/overfit-tools-at.pdf (backtest overfitting) · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 (Deflated Sharpe)
- https://link.springer.com/article/10.1007/s44163-025-00424-4 (cost-aware FX ML) · https://arxiv.org/pdf/1911.10107 (RL cost threshold)
- https://bettersystemtrader.com/192-predicting-profitability-using-machine-learning-ernie-chan/

*Note on confidence: HF repo paths and Apache/MIT licenses for the major models (Chronos, TimesFM, TOTO, Moirai, Fin-R1, SB3, gym-anytrading) were confirmed against primary sources. A handful of smaller models' licenses come from search snippets — flagged inline as "verify." FinCast and some FinLlama/TradingAgents weight paths/licenses are unverified.*
