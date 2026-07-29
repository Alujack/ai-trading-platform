"""ml_platform — serves the models this platform trains itself.

Closes the loop that `training/` was built for. Before this module the pipeline
stopped one step short of usable:

    build_dataset.py -> train.py -> training/models/{SYMBOL}_{TF}.txt -> (nothing)

`build_dataset.py`'s docstring already claimed it used "the SAME
`features.build_feature_row` the live strategy calls" — but no live strategy
called it. The platform could train a model and had no way to run one. This is
that strategy, and it is what makes the claim true.

Contrast with `ml_xau`, the vendored xaubot port. That module reproduces a
frozen ONNX binary and therefore carries its own feature code. This one imports
`training.features` directly, so training and inference are physically the same
function — the divergence that broke upstream is not expressible here.

Three couplings are load-bearing, all of them to `training/`:

1. **`lookback` is `features.LOOKBACK`**, not a number typed in again. The ICT
   detectors scan the whole window, so a window of a different length can
   surface a different swing or order block and quietly change the features.
2. **Stop and target default to `labels.LabelConfig`'s geometry** (1.5x ATR,
   2.0 RR). The labels were produced by simulating exactly that trade. Execute
   a different one and the model's "LONG wins here" is a claim about a trade
   nobody is taking — which is how a model can be right and still lose money.
3. **The model is resolved per (symbol, timeframe)**, matching how `train.py`
   writes `{symbol}_{timeframe}.txt`. One registered strategy serves every
   trained model rather than needing one registry entry each.

UNVALIDATED, like everything else here: it must clear backtester.py,
walkforward.py and a Monte-Carlo against `ml_platform_random` before it is
enabled. Measured edge over majority class at time of writing was +0.0019 on
15min and negative on 1min/5min — see `training/models/*_metrics.json`.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from training.features import LOOKBACK, build_feature_row

from .base import RANGING, TRENDING, VOLATILE, BarWindow, SignalCandidate
from .ml_xau import _coin

log = logging.getLogger("data.strategies.ml_platform")

# Mirrors `training.labels`, which cannot be imported at module scope: it pulls
# in `backtest.engine` for its cost model, and `backtest.engine` imports this
# package — so a top-level import here closes the cycle and breaks every entry
# point that touches the registry.
#
# Duplicating three ints is the smaller evil, but duplication is exactly how
# training and inference drift apart, so
# `tests/test_ml_platform.py::test_label_encoding_matches_training` asserts
# these against the real definitions. Change one, the test fails.
SHORT, HOLD, LONG = 0, 1, 2
_FALLBACK_ATR_STOP_MULT = 1.5
_FALLBACK_RR = 2.0


def _label_geometry() -> tuple[float, float]:
    """`LabelConfig`'s stop/RR, imported lazily to dodge the cycle above."""
    try:
        from training.labels import LabelConfig

        cfg = LabelConfig()
        return float(cfg.atr_stop_mult), float(cfg.rr)
    except ImportError:  # pragma: no cover — only if the cycle is hit anyway
        return _FALLBACK_ATR_STOP_MULT, _FALLBACK_RR

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "training" / "models"

# Booster objects are cheap to query and expensive to load — cache per path.
# `None` is cached too, so a missing model logs once instead of once per bar.
_BOOSTERS: dict[str, Any] = {}


def _booster(model_path: str) -> Any:
    if model_path in _BOOSTERS:
        return _BOOSTERS[model_path]
    booster = None
    if os.path.exists(model_path):
        try:
            import lightgbm as lgb  # lazy: the worker starts without lightgbm

            booster = lgb.Booster(model_file=model_path)
        except Exception as exc:  # noqa: BLE001 — a bad model must not kill the scan
            log.error("ml_platform_load_failed path=%s err=%s", model_path, exc)
    else:
        log.warning("ml_platform_no_model path=%s — train one first", model_path)
    _BOOSTERS[model_path] = booster
    return booster


class MlPlatform:
    """LightGBM 3-class classifier over `training.features` → ≤1 candidate/bar."""

    name = "ml_platform"
    # Not regime-gated: `regime_trending/ranging/volatile` are model inputs, so
    # the classifier decides for itself what the regime implies.
    regimes = {TRENDING, RANGING, VOLATILE}
    lookback = LOOKBACK

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        stop_mult, rr = _label_geometry()
        self.min_confidence = float(p.get("minConfidence", 0.50))
        # Defaults deliberately mirror the labelling geometry — see module docstring.
        self.atr_stop_mult = Decimal(str(p.get("atrStopMult", stop_mult)))
        self.rr = Decimal(str(p.get("riskReward", rr)))
        self._explicit_path = p.get("modelPath") or os.environ.get("ML_PLATFORM_MODEL")
        self._model_dir = Path(p.get("modelDir") or _DEFAULT_MODEL_DIR)

    def _resolve_model(self, symbol: str, timeframe: str) -> str:
        if self._explicit_path:
            return str(self._explicit_path)
        return str(self._model_dir / f"{symbol}_{timeframe}.txt")

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars
        if len(bars) < self.lookback:
            return []

        booster = _booster(self._resolve_model(window.symbol, window.timeframe))
        if booster is None:
            return []

        # BarWindow is most-recent-first; the feature builder wants chronological
        # and reads the LAST bar as the decision bar.
        chron = list(reversed(bars))[-self.lookback :]
        row = build_feature_row(chron, timeframe=window.timeframe)
        if row is None:
            return []

        try:
            proba = np.asarray(booster.predict(row.reshape(1, -1))).ravel()
        except Exception as exc:  # noqa: BLE001
            log.error("ml_platform_inference_failed err=%s", exc)
            return []
        if proba.size != 3:
            log.error("ml_platform_bad_output size=%d (expected 3 classes)", proba.size)
            return []

        label = int(np.argmax(proba))
        if label == HOLD:
            return []
        confidence = float(proba[label])
        if confidence < self.min_confidence:
            return []

        latest = chron[-1]
        atr = latest.atr
        if atr is None or atr <= 0:
            return []  # no ATR means no stop we can defend — skip, never impute

        entry = latest.close
        risk = self.atr_stop_mult * atr
        direction = "LONG" if label == LONG else "SHORT"
        if direction == "LONG":
            stop, target = entry - risk, entry + self.rr * risk
        else:
            stop, target = entry + risk, entry - self.rr * risk

        return [
            SignalCandidate(
                strategy_name=self.name,
                symbol=window.symbol,
                timeframe=window.timeframe,
                direction=direction,
                entry=entry,
                stop=stop,
                target=target,
                confidence=max(0, min(90, int(confidence * 100))),
                reasoning=(
                    f"{self.name} LightGBM {direction} p={confidence:.3f} "
                    f"(>= {self.min_confidence}); stop {self.atr_stop_mult}xATR, {self.rr}:1 RR"
                ),
                client_id=(
                    f"{self.name}|{window.symbol}|{window.timeframe}|{direction}"
                    f"|{latest.timestamp.isoformat()}"
                ),
            )
        ]


class MlPlatformRandomBaseline(MlPlatform):
    """Timing- and geometry-matched random control for `ml_platform`.

    Fires on exactly the bars the model clears its threshold on, with the
    identical ATR stop and RR target, but flips a coin for direction. That
    isolates the only thing the model actually contributes over its own frame:
    the choice of side. Same contract as `ict_random_baseline` and
    `ml_xau_random` — see `baseline_mc.py` for how to read the result.

    This matters more here than it looks. `ml_xau` scored a 34.5% win rate on
    2026 XAUUSD 1min and its random control scored 34.5% too, which is how we
    know its ranking was worth nothing. Any model served by `MlPlatform` gets
    held to the same test.
    """

    name = "ml_platform_random"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.seed = int((params or {}).get("seed", 0))

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        out = super().evaluate(window)
        if not out:
            return out
        sig = out[0]
        rng = _coin(self.seed, sig.client_id or "")
        direction = "LONG" if rng.random() < 0.5 else "SHORT"
        if direction != sig.direction:
            # Flip the frame around the same entry, preserving |risk| and RR.
            risk = abs(sig.entry - sig.stop)
            rr = abs(sig.target - sig.entry) / risk if risk > 0 else self.rr
            sig.direction = direction
            sig.stop = sig.entry - risk if direction == "LONG" else sig.entry + risk
            sig.target = (
                sig.entry + rr * risk if direction == "LONG" else sig.entry - rr * risk
            )
        sig.strategy_name = self.name
        sig.reasoning = (
            f"{self.name} control (seed={self.seed}) {direction}; frame matched to ml_platform"
        )
        sig.client_id = f"{self.name}|{window.symbol}|{window.timeframe}|{direction}|{sig.entry}"
        return [sig]
