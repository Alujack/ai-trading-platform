"""ml_xau — LightGBM/ONNX classifier ported from the external `xaubot` project.

Provenance: `andywarui/xaubot`, model `MT5_XAUBOT/Files/lightgbm_real_26features.onnx`
(the only shipped ONNX that is real bytes rather than a Git-LFS pointer stub).
Trained on 2022-2024 Kaggle XAUUSD **M1** data, so this strategy is only
meaningful on the 1min timeframe.

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

import logging
import os
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
    """pandas .ewm(span, adjust=False, min_periods=1) equivalent."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


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
