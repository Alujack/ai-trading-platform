"""ml_xau — LightGBM/ONNX classifier ported from the external `xaubot` project.

FROZEN — LEGACY CONTRACT. DO NOT REFACTOR.
==========================================
This module deliberately carries its own feature implementation, which is the
one exception to the "exactly one code path" rule that `training/features.py`
opens with. The reason is that the 26-feature contract below is not a design
choice we own — it is a property of the compiled ONNX binary. LightGBM binds
inputs by position, so *any* change to `FEATURE_ORDER` or to the indicator
formulas silently feeds the model different numbers than it was fit on, and
nothing fails loudly when that happens.

So: this file is a faithful reproduction of a fixed artifact, not a strategy
under active development. New model work belongs in `training/features.py`,
which is shared by `training/build_dataset.py` and the live path and has no
frozen binary to satisfy. `tests/test_ml_features.py::test_ml_xau_contract_frozen`
pins the contract so a well-meaning refactor breaks the build instead of the
model.

Provenance: `andywarui/xaubot`, model `MT5_XAUBOT/Files/lightgbm_real_26features.onnx`
(the only shipped ONNX that is real bytes rather than a Git-LFS pointer stub).
Trained on 2022-2024 Kaggle XAUUSD **M1** data, so this strategy is only
meaningful on the 1min timeframe. Note our own execution broker only has 1min
history back to ~2026-04, so this is also the timeframe on which we have the
least data to validate it — see `research/xaubot/IMPORT_NOTES.md`.

The upstream project advertises 66.2% win rate / 3,780% return. Those numbers do
not survive review and are NOT the reason this exists — see the port notes in
`docs/research/` and the caveats below. This module is the honest re-port: it
reproduces the model's feature contract exactly, then lets the platform's own
backtester / walkforward / random-baseline harness decide whether there is edge.

Caveats baked into the upstream model (verified, not assumed):
  * 10 of its 26 inputs (`mtf_0..mtf_9`) are hard-coded 0.0 — placeholders for a
    Transformer component that was never shipped. It is a 16-feature model
    wearing a 26-feature interface.
  * On 2026 XAUUSD 1min it is degenerate: ~88% HOLD, ~12% SHORT, ~0% LONG
    (training distribution was 51/27/22). It effectively only ever sells.
  * `ema_10/20/50`, `tr` and `atr_14` are raw price-scale features, so the model
    is sensitive to the absolute price level it was trained at.

Feature order is load-bearing: it must match
`python_training/train_real_26features_optimized.py` exactly, and the indicator
formulas must match `python_backtesting/prepare_real_data.py` (note their RSI and
ATR use simple rolling means, NOT Wilder's smoothing).
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from .base import RANGING, TRENDING, VOLATILE, BarWindow, SignalCandidate

log = logging.getLogger("data.strategies.ml_xau")

# Exact upstream feature order. Do not reorder — LightGBM binds by position.
FEATURE_ORDER: tuple[str, ...] = (
    "body", "body_abs", "candle_range", "close_position",
    "return_1", "return_5", "return_15", "return_60",
    "tr", "atr_14", "rsi_14",
    "ema_10", "ema_20", "ema_50",
    "hour_sin", "hour_cos",
) + tuple(f"mtf_{i}" for i in range(10))

# Upstream label encoding.
SHORT, HOLD, LONG = 0, 1, 2

_DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "lightgbm_real_26features.onnx"

# onnxruntime sessions are thread-safe and expensive to build — cache per path.
_SESSIONS: dict[str, Any] = {}


def _session(model_path: str) -> Any:
    sess = _SESSIONS.get(model_path)
    if sess is None:
        import onnxruntime as ort  # imported lazily so the worker starts without onnxruntime

        opts = ort.SessionOptions()
        opts.log_severity_level = 3  # silence the benign output-shape warning
        sess = ort.InferenceSession(model_path, sess_options=opts)
        _SESSIONS[model_path] = sess
    return sess


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    """pandas .ewm(span, adjust=False, min_periods=1) equivalent, vectorised.

    The recurrence ema[i] = a*x[i] + (1-a)*ema[i-1] unrolls to a weighted sum,
    which cumsum evaluates in one pass. The intermediate 1/(1-a)^i grows, but the
    strategy only ever calls this on a `lookback`-sized window (100 bars), where
    the largest factor is ~1e2 in float64 — a scalar loop here cost ~300 Python
    iterations per evaluated bar, which dominated walk-forward runtime.

    That window bound is load-bearing, not incidental. `decay[i]` underflows to
    0.0 once (1-a)^i drops below ~1e-308, and the division on the next line then
    yields inf -> NaN -> `nan_to_num` turns it into a perfectly innocent-looking
    0.0. Measured first-bad length: span 10 at ~3,550 bars, span 20 at ~7,050.
    Both live (`evaluate`) and backtest (`backtest/engine.py` trailing window)
    pass exactly `lookback` bars, so nothing currently reaches this — but a batch
    caller over a full series would get silently zeroed EMAs with no warning at
    all. Raise instead, because the frozen contract means we cannot fix the math
    without changing what the model sees.
    """
    n = len(values)
    if n == 0:
        return values.astype(np.float64)
    a = 2.0 / (span + 1.0)
    x = values.astype(np.float64)
    max_safe = int(-308.0 / math.log10(1.0 - a))
    if n > max_safe:
        raise ValueError(
            f"_ema(span={span}) called on {n} bars; the vectorised form silently "
            f"returns zeros past ~{max_safe}. Score bar-by-bar over a "
            f"{MlXau.lookback}-bar window (see build_features' docstring)."
        )
    decay = (1.0 - a) ** np.arange(n)
    # ema[i] = decay[i]*x[0] + a * sum_{j=1..i} decay[i-j]*x[j]
    contrib = np.zeros(n)
    contrib[1:] = a * x[1:] / decay[1:]
    return decay * (x[0] + np.cumsum(contrib))


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    """pandas .rolling(window, min_periods=1).mean() equivalent."""
    csum = np.cumsum(np.insert(values, 0, 0.0))
    idx = np.arange(len(values))
    lo = np.maximum(0, idx - window + 1)
    return (csum[idx + 1] - csum[lo]) / (idx - lo + 1)


def build_features(
    ts: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray
) -> np.ndarray:
    """Build the upstream 26-feature matrix from chronological OHLC arrays.

    Mirrors prepare_real_data.py + train_real_26features_optimized.py. Returns
    shape (len(c), 26) float32.

    Call this on a `MlXau.lookback`-sized window and read the LAST row, the way
    `evaluate` does. Two reasons, both measured:

    * `_ema` raises past ~3,550 bars (see its docstring).
    * The EMAs are path-dependent, so the row you get for a given bar depends on
      how much history preceded it. At 100 bars, `ema_50` sits ~0.11 price units
      away from its full-series value. Upstream trained on full-series pandas
      `ewm`, so there is a permanent, systematic train/live skew on the three raw
      price-scale EMA features here. It cannot be fixed from this side — closing
      it would mean changing what the frozen ONNX binary is fed. It is one more
      reason this model is a validation subject rather than a trading strategy.
    """
    n = len(c)
    f: dict[str, np.ndarray] = {}

    body = c - o
    f["body"] = body
    f["body_abs"] = np.abs(body)
    f["candle_range"] = h - l
    f["close_position"] = (c - l) / (h - l + 1e-8)

    for k in (1, 5, 15, 60):
        r = np.full(n, np.nan)
        if n > k:
            r[k:] = c[k:] / c[:-k] - 1.0
        f[f"return_{k}"] = r

    prev_c = np.concatenate(([np.nan], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    tr[0] = h[0] - l[0]  # no prior close on the first bar
    f["tr"] = tr
    f["atr_14"] = _rolling_mean(tr, 14)

    delta = np.concatenate(([np.nan], np.diff(c)))
    gain = np.where(np.nan_to_num(delta) > 0, np.nan_to_num(delta), 0.0)
    loss = np.where(np.nan_to_num(delta) < 0, -np.nan_to_num(delta), 0.0)
    avg_gain = _rolling_mean(gain, 14)
    avg_loss = _rolling_mean(loss, 14)
    f["rsi_14"] = 100.0 - (100.0 / (1.0 + avg_gain / (avg_loss + 1e-10)))

    for span in (10, 20, 50):
        f[f"ema_{span}"] = _ema(c, span)

    hours = ts.astype("datetime64[h]").astype(int) % 24
    f["hour_sin"] = np.sin(2 * np.pi * hours / 24.0)
    f["hour_cos"] = np.cos(2 * np.pi * hours / 24.0)

    # Placeholders for the Transformer that upstream never shipped.
    for i in range(10):
        f[f"mtf_{i}"] = np.zeros(n)

    mat = np.column_stack([f[name] for name in FEATURE_ORDER])
    # Upstream does .ffill().bfill().fillna(0); only the warmup head is NaN here.
    return np.nan_to_num(mat, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


class MlXau:
    """ONNX LightGBM classifier → at most one SignalCandidate per bar."""

    name = "ml_xau"
    # Not regime-gated: the classifier is supposed to encode regime itself.
    regimes = {TRENDING, RANGING, VOLATILE}
    # 60 bars for return_60 + upstream's 100-bar warmup before it trusts features.
    lookback = 100

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.min_confidence = float(p.get("minConfidence", 0.55))
        self.atr_stop_mult = Decimal(str(p.get("atrStopMult", 1.5)))
        self.rr = Decimal(str(p.get("riskReward", 2.0)))
        self.model_path = str(p.get("modelPath") or os.environ.get("ML_XAU_MODEL") or _DEFAULT_MODEL)

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars
        if len(bars) < self.lookback:
            return []
        # BarWindow is most-recent-first; the feature math needs chronological.
        chron = list(reversed(bars))
        if any(b.open is None or b.high is None or b.low is None for b in chron):
            return []

        ts = np.array([np.datetime64(b.timestamp) for b in chron])
        o = np.array([float(b.open) for b in chron])       # type: ignore[arg-type]
        h = np.array([float(b.high) for b in chron])       # type: ignore[arg-type]
        l = np.array([float(b.low) for b in chron])        # type: ignore[arg-type]
        c = np.array([float(b.close) for b in chron])

        feats = build_features(ts, o, h, l, c)[-1:]  # score the newest bar only

        try:
            label_out, prob_out = _session(self.model_path).run(None, {"input": feats})
        except Exception as exc:  # noqa: BLE001 — a bad model must not kill the scan
            log.error("ml_xau_inference_failed err=%s", exc)
            return []

        label = int(np.asarray(label_out).ravel()[0])
        if label == HOLD:
            return []
        # ONNX ZipMap: probabilities come back as a sequence of {class: prob}.
        probs = prob_out[0]
        confidence = float(probs[label] if isinstance(probs, dict) else np.asarray(probs).ravel()[label])
        if confidence < self.min_confidence:
            return []

        latest = chron[-1]
        atr = latest.atr
        if atr is None or atr <= 0:
            # Fall back to the locally computed ATR so a missing Indicator row
            # does not silently drop every signal.
            atr = Decimal(str(float(_rolling_mean(
                np.maximum(h - l, np.maximum(
                    np.abs(h - np.concatenate(([c[0]], c[:-1]))),
                    np.abs(l - np.concatenate(([c[0]], c[:-1]))))), 14)[-1])))
        if atr <= 0:
            return []

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
                # Map model probability (min_confidence..1.0) onto a 0-90 band.
                confidence=max(0, min(90, int(confidence * 100))),
                reasoning=(
                    f"ml_xau LightGBM/ONNX {direction} p={confidence:.3f} "
                    f"(>= {self.min_confidence}); stop {self.atr_stop_mult}xATR, {self.rr}:1 RR"
                ),
                client_id=f"ml_xau|{window.symbol}|{window.timeframe}|{direction}|{latest.timestamp.isoformat()}",
            )
        ]


def _coin(seed: int, ts_iso: str) -> random.Random:
    """Per-(seed, bar) RNG, reproducible across processes (PYTHONHASHSEED-proof)."""
    digest = hashlib.sha1(f"{seed}|{ts_iso}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


class MlXauRandomBaseline(MlXau):
    """Geometry- AND timing-matched random control for ml_xau.

    ml_xau turns out to be effectively short-only on 2026 gold (LONG is never
    predicted), so its entire contribution reduces to two things: *when* it
    fires, and the fixed SHORT direction. This control keeps the timing — it
    fires on exactly the bars the model clears its threshold on — and keeps the
    identical ATR stop / RR target geometry, but replaces the direction with a
    coin flip.

    That isolates the one question a positive backtest cannot answer on its own:
    is "always SHORT" skill, or was it a directional bet that happened to match
    the sample window? ml_xau only has a real edge if it lands in the right tail
    of a Monte-Carlo over this baseline's seeds. Mirrors the ict_confluence /
    ict_random_baseline pattern (build plan §8).
    """

    name = "ml_xau_random"

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
        if direction == sig.direction:
            return [sig]
        # Flip the frame around the same entry, preserving |risk| and RR exactly.
        risk = abs(sig.entry - sig.stop)
        rr = abs(sig.target - sig.entry) / risk if risk > 0 else self.rr
        if direction == "LONG":
            stop, target = sig.entry - risk, sig.entry + rr * risk
        else:
            stop, target = sig.entry + risk, sig.entry - rr * risk
        sig.direction = direction
        sig.stop = stop
        sig.target = target
        sig.strategy_name = self.name
        sig.reasoning = f"ml_xau_random control (seed={self.seed}) {direction}; frame matched to ml_xau"
        sig.client_id = f"ml_xau_random|{window.symbol}|{window.timeframe}|{direction}|{sig.entry}"
        return [sig]
