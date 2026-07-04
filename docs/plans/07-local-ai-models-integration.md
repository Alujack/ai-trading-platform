# Development Plan — Local AI Models Integration

**Goal:** Integrate open-source, self-hostable AI models into the platform as **signal-enhancing features**, starting with FinBERT news sentiment, then layering in volatility forecasting, local LLM reasoning, and RL research.

**Guiding principle (from research):** No model predicts forex direction reliably after costs. Models are **inputs to a disciplined system**, never standalone trade oracles. Every model output flows through the existing signal gate → risk engine → backtest → paper-trade pipeline. This matches the rules in `CLAUDE.md`.

**Hardware target:** RTX 3070 (8GB VRAM). This constrains what runs concurrently — small models (FinBERT, Chronos-2, Granite TTM) are trivial; a 7B LLM only fits at 4-bit quantization and can't share VRAM with other large models. Plan assumes **one large model loaded at a time**, small models on CPU where possible.

**Scope:** Phased MVP → full. Ship Phase 1 end-to-end before starting Phase 2.

---

## Current state (verified from codebase)

| Component | Status | Relevance |
|---|---|---|
| `services/ai` FastAPI | ✓ Provider pattern (mock/Anthropic/Gemini) in `src/providers.py` | Add a local-model provider/module here |
| Signal gate | ✓ `apps/api/src/signals/gate.ts` — calls AI, runs risk engine | Inject sentiment/forecast as context here |
| Risk engine | ✓ `apps/api/src/risk/riskEngine.ts` — reads `NewsEvent` for blackout | Already consumes news; sentiment extends it |
| `NewsEvent` table | ⚠️ **0 rows — news ingestion NOT implemented** | **Phase 1 dependency** |
| Backtester | ✓ `services/data/backtester.py` + `BacktestRun` table | Extend with validation harness |
| Local ML models | ✗ None — LLM APIs only | This plan introduces them |
| GPU serving | ✗ None | Phase 1 adds the serving foundation |

The platform is architecturally ready. This plan adds a **local model-serving capability** to `services/ai` and wires outputs into the existing flow — no rearchitecting required.

---

## Phase 0 — Foundation (½–1 day)

Set up local model serving without changing trading behavior. Low risk, unblocks everything.

1. **Add ML dependencies** to `services/ai/requirements.txt`: `transformers`, `torch` (CUDA build for the 3070), `sentencepiece`, `accelerate`. Pin versions.
2. **GPU in Docker:** add `deploy.resources.reservations.devices` (NVIDIA) to the `ai` service in `docker-compose.yml`, or run the AI service on the host for GPU access during dev. Verify `torch.cuda.is_available()`.
3. **Local-model module** `services/ai/src/models/` — a `ModelRegistry` that lazy-loads HF models, with a VRAM-aware policy (load on first use, optional unload to free VRAM). Mirrors the existing `providers.py` abstraction style.
4. **Health surfacing:** extend `/health` to report loaded models, device (cuda/cpu), and VRAM usage.

**Exit criteria:** `services/ai` boots, reports CUDA available, can load and unload a dummy HF model. No trading logic touched.

---

## Phase 1 — FinBERT News Sentiment (MVP) (3–5 days)

The chosen first milestone. Delivers a per-currency sentiment feature into the signal gate. **Requires news ingestion**, so we build that here too.

### 1a. News ingestion (the real blocker)
- Implement `services/data/news_fetcher.py`: ForexFactory calendar (high-impact events) + a news headline source (Alpha Vantage or similar).
- Upsert into existing `NewsEvent` table (title, impact, currency, scheduledAt, forecast/actual/previous).
- Schedule via the existing worker / n8n (`NEWS_SUMMARY_URL` already wired to `/analyze/news-summary`).
- **Side benefit:** this alone activates the risk engine's currently-dormant 30-min news blackout.

### 1b. FinBERT sentiment endpoint
- Model: `ProsusAI/finbert` (Apache-2.0, ~110M — runs on CPU, leaves VRAM free). Fallback/comparison: `mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis` (faster).
- New endpoint `POST /analyze/news-sentiment` in `services/ai/src/main.py`: input headlines → output `{label, score}` per item, aggregated **per currency**.
- Add a `NewsSentiment` field/table or extend `NewsEvent.aiSummary` with a structured sentiment score.
- **FX-aware aggregation:** score per currency, then derive a pair bias (e.g. EUR sentiment − USD sentiment for EUR/USD), since forex sentiment is relational. Document that off-the-shelf FinBERT is equity-trained — treat as a weak prior until fine-tuned.

### 1c. Wire into the signal gate
- In `apps/api/src/signals/gate.ts`, fetch recent per-currency sentiment alongside candles/indicators and pass it into the AI validation context and persist it on the `Signal`.
- Sentiment is **context/feature only** — it does not bypass the risk engine.

### 1d. Validate before trusting
- Backtest: does adding sentiment as a feature improve paper-trade outcomes vs. baseline? Use the `backtester.py` + `BacktestRun`.
- Run in shadow mode (logged, not acted on) for a period, then paper-trade.

**Exit criteria:** `NewsEvent` populated; news blackout active; sentiment scored per currency and visible on signals; shadow/backtest comparison logged. No live money.

---

## Phase 2 — Volatility Forecasting (Chronos-2) (4–6 days)

Forecast volatility/levels for **position sizing and regime detection** — not direction (research shows TSFMs fail at return direction).

- Model: `amazon/chronos-2` (Apache-2.0, ~120M, native covariate support — feed your existing ATR/RSI/EMA indicators). Fits comfortably in VRAM. Alternative: `ibm-granite/granite-timeseries-ttm-r2` (tiny, CPU).
- Endpoint `POST /analyze/volatility-forecast`: input recent candles + indicators → probabilistic forecast (quantiles).
- **Use the output for:** dynamic position sizing in the risk engine (size down when forecast volatility is high), and a regime flag (trending vs. choppy) the gate can use to enable/disable strategies.
- Integrate as a `RiskConfig`-adjacent input, respecting the existing hierarchical config resolution.
- Validate: compare risk-adjusted paper-trade performance with vs. without volatility-scaled sizing.

**Exit criteria:** volatility forecast available to the risk engine; position sizing optionally scales with it; backtested improvement (or honest null result) documented.

---

## Phase 3 — Local LLM Reasoning (Fin-R1) (5–7 days)

Replace/augment the paid Claude/Gemini calls with a self-hosted finance LLM for **journaled reasoning and compliance checks**.

- Model: `SUFE-AIFLM-Lab/Fin-R1` (Apache-2.0, 7B, Qwen2.5-based).
- **VRAM reality (3070, 8GB):** serve via **Ollama or llama.cpp with Q4 quantization** (~4.5GB) rather than full-precision transformers. Cannot run alongside another large model — load on demand. Keep FinBERT/Chronos small/CPU so they don't compete for VRAM.
- Add a **`local` provider** to the existing `providers.py` abstraction (OpenAI-compatible endpoint from Ollama) so `/analyze/validate-signal`, `/analyze/journal-review`, and `/analyze/trade-review` can route to it. This slots into the current provider-switching design with zero new plumbing.
- Use Fin-R1's chain-of-thought output to populate the `Journal.aiReview` and the reasoning your `CLAUDE.md` requires for every signal.
- Keep Claude/Gemini as fallback (provider switch already supports this).
- Validate: compare Fin-R1 vs. Claude validation decisions on historical signals; check latency is acceptable for the 5-min gate cadence.

**Exit criteria:** local LLM serves signal validation + journaling; provider-switchable; quality/latency benchmarked against the API baseline.

---

## Phase 4 — RL Strategy Research (sandbox, not production) (ongoing)

Pure research track. RL agents mostly don't survive live FX (non-stationarity, costs) — keep it sandboxed.

- Stack: `gym-anytrading` (native `ForexEnv`) or `gym-mtsim` (MT5 realism) + `stable-baselines3` (PPO). All MIT.
- Feed your TimescaleDB candles into a custom gym env; train offline; persist agent; serve `model.predict(obs)` as just another `SignalCandidate` source via the existing strategy contract (`services/data/strategies/base.py`).
- RL candidates flow through the **same gate + risk engine** — no special path.
- Treat all backtest returns as contaminated until reproduced out-of-sample with realistic FX spreads.

**Exit criteria:** an RL agent emits candidates into the normal pipeline; evaluated honestly; no live deployment without surviving the validation harness.

---

## Cross-cutting — Validation Harness (build alongside Phase 1, used by all)

The research is emphatic that backtests lie. Harden `services/data/backtester.py`:

- **Purged / combinatorial cross-validation** (avoid lookahead/leakage in time-series labels).
- **Deflated Sharpe Ratio** and multiple-testing correction (a Sharpe ≥ 1.0 appears by chance after ~45 trials).
- **Realistic FX costs:** spread, slippage, commission baked into every backtest.
- **Walk-forward** evaluation as the default.
- Make passing this harness a **gate** before any model's output influences paper trading, and before paper → live.

This becomes the objective bar every phase must clear.

---

## Sequencing & milestones

```
Phase 0  Foundation (GPU serving)        ──┐
Phase 1  FinBERT + News ingestion          │  ← MVP, ship end-to-end
         + Validation harness (parallel)   │
Phase 2  Chronos-2 volatility            ──┤  ← after Phase 1 proven
Phase 3  Fin-R1 local LLM reasoning      ──┤
Phase 4  RL research sandbox             ──┘  ← ongoing, low priority
```

Rough estimate (solo): **~3–4 weeks to a validated Phase 1**, Phases 2–3 another ~3 weeks, Phase 4 ongoing.

## Risks & decisions to make

- **VRAM contention (8GB):** can't run Fin-R1 + Chronos + FinBERT all on GPU at once. Decision: small models on CPU, Fin-R1 via quantized Ollama, load-on-demand. Revisit if you upgrade the GPU.
- **FinBERT is equity-trained:** off-the-shelf FX value is marginal. Decision point after Phase 1: invest in fine-tuning on forex/macro news, or keep as a weak prior.
- **Licenses:** every model in this plan is Apache-2.0/MIT (commercial-safe). Avoid Moirai, Palmyra-Fin, InvestLM, TimeGPT (non-commercial/closed) — see the research report.
- **Latency:** local 7B inference must fit the 5-min gate cadence; benchmark in Phase 3 before committing.

## Definition of done (per phase)

A model is "done" only when: it has a working endpoint in `services/ai`, its output is wired into the gate/risk engine as a feature, it has passed the validation harness with realistic costs, and it has run in shadow + paper mode — never straight to live. This mirrors the `CLAUDE.md` rules: risk engine before execution, journal every signal, backtest before live, paper before real.

---

*Companion document: `docs/AI-Trading-Models-Research.md` (model comparison, licenses, sources).*
