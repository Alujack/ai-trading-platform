"""Contract tests for `strategies/ml_platform.py`.

This strategy exists to serve the models `training/` produces, so almost
everything that can go wrong is a coupling silently drifting apart from
`training/`. These tests pin the three couplings that would produce a model
scoring one thing while the platform trades another — none of which fail loudly
on their own.

Pure logic plus one on-disk model check; no DB. Runnable under pytest, or
directly: ``python tests/test_ml_platform.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

# Import order matters: `strategies` first, so that when `training.labels`
# later pulls in `backtest.engine` the strategies package is already resolved.
# This is the cycle `ml_platform` avoids with a lazy import — see its comments.
from strategies import BarWindow, build_strategy  # noqa: E402
from strategies import ml_platform as MP  # noqa: E402
from strategies.base import IndicatorBar  # noqa: E402
from training.features import LOOKBACK  # noqa: E402

START = datetime(2026, 1, 5, 0, 0)


def _bars(n: int, seed: int = 5) -> list[IndicatorBar]:
    """Chronological synthetic bars with every indicator `build_feature_row` reads."""
    rng = np.random.default_rng(seed)
    price = 2400.0
    out: list[IndicatorBar] = []
    closes: list[float] = []
    for i in range(n):
        price = max(100.0, price + rng.normal(0, 3.0))
        o = price + rng.normal(0, 0.5)
        c = price
        span = abs(rng.normal(0, 2.0)) + 0.5
        closes.append(c)
        atr = max(0.5, float(np.std(closes[-14:])) + 1.0)
        out.append(
            IndicatorBar(
                timestamp=START + timedelta(minutes=15 * i),
                close=Decimal(str(round(c, 3))), open=Decimal(str(round(o, 3))),
                high=Decimal(str(round(max(o, c) + span, 3))),
                low=Decimal(str(round(min(o, c) - span, 3))),
                rsi=Decimal("55"),
                ema20=Decimal(str(round(float(np.mean(closes[-20:])), 3))),
                ema50=Decimal(str(round(float(np.mean(closes[-50:])), 3))),
                ema200=Decimal(str(round(float(np.mean(closes[-200:])), 3))),
                atr=Decimal(str(round(atr, 3))),
                bb_lower=Decimal(str(round(c - 2 * atr, 3))),
                bb_upper=Decimal(str(round(c + 2 * atr, 3))),
                bb_pctb=Decimal("0.5"), adx=Decimal("22"),
            )
        )
    return out


def _window(n: int = LOOKBACK, timeframe: str = "15min") -> BarWindow:
    # BarWindow is most-recent-first, matching the live runner's contract.
    return BarWindow(symbol="XAUUSD", timeframe=timeframe, bars=list(reversed(_bars(n))))


def test_label_encoding_matches_training() -> None:
    """`ml_platform` re-declares SHORT/HOLD/LONG because it cannot import
    `training.labels` at module scope (circular). This is the guard that keeps
    the copy honest — if the real encoding ever changes, the model's class
    indices would be silently reinterpreted."""
    from training.labels import HOLD, LONG, SHORT

    assert (MP.SHORT, MP.HOLD, MP.LONG) == (SHORT, HOLD, LONG)


def test_geometry_matches_label_config() -> None:
    """The stop/target actually placed must equal the one the labels simulated.

    If these diverge, the model is right about a trade nobody takes: it learned
    "a 1.5x-ATR stop at 2:1 resolves as a win here" and the platform then opens
    something else entirely.
    """
    from training.labels import LabelConfig

    cfg = LabelConfig()
    s = build_strategy("ml_platform")
    assert float(s.atr_stop_mult) == cfg.atr_stop_mult
    assert float(s.rr) == cfg.rr


def test_lookback_is_bound_to_features_not_retyped() -> None:
    """ICT detectors scan the whole window, so a different length can surface a
    different swing and change the features for the same bar."""
    assert build_strategy("ml_platform").lookback == LOOKBACK


def test_resolves_model_per_symbol_and_timeframe() -> None:
    """One registry entry serves every trained model — `train.py` writes
    `{symbol}_{timeframe}.txt`, so resolution must vary on both."""
    s = build_strategy("ml_platform")
    a = s._resolve_model("XAUUSD", "15min")
    b = s._resolve_model("EURUSD", "60min")
    assert a.endswith("XAUUSD_15min.txt")
    assert b.endswith("EURUSD_60min.txt")
    assert a != b


def test_explicit_model_path_overrides_resolution() -> None:
    s = build_strategy("ml_platform", {"modelPath": "/tmp/pinned.txt"})
    assert s._resolve_model("XAUUSD", "15min") == "/tmp/pinned.txt"


def test_missing_model_yields_no_signals_and_does_not_raise() -> None:
    """A missing model must degrade to silence, not take down the scan — the
    runner evaluates every strategy on every bar for every symbol."""
    s = build_strategy("ml_platform", {"modelPath": "/tmp/definitely-not-a-model.txt"})
    assert s.evaluate(_window()) == []


def test_short_window_yields_no_signals() -> None:
    s = build_strategy("ml_platform")
    assert s.evaluate(_window(LOOKBACK - 1)) == []


def test_signal_geometry_is_internally_consistent() -> None:
    """Whatever the model says, the emitted frame must be a coherent trade:
    stop on the losing side, target on the winning side, at the configured RR."""
    s = build_strategy("ml_platform", {"minConfidence": 0.0})
    if not os.path.exists(s._resolve_model("XAUUSD", "15min")):
        return  # no trained model in this checkout — nothing to assert
    out = s.evaluate(_window())
    if not out:
        return  # model predicted HOLD on the fixture; geometry is untested but valid
    sig = out[0]
    risk = abs(sig.entry - sig.stop)
    reward = abs(sig.target - sig.entry)
    assert risk > 0
    assert abs(float(reward / risk) - float(s.rr)) < 1e-6
    if sig.direction == "LONG":
        assert sig.stop < sig.entry < sig.target
    else:
        assert sig.target < sig.entry < sig.stop


def test_random_baseline_preserves_the_frame_it_flips() -> None:
    """The control must differ from the model in exactly one respect: side.

    If it also changed risk or RR, a baseline comparison would be measuring the
    geometry rather than the model's choice of direction.
    """
    s = build_strategy("ml_platform", {"minConfidence": 0.0})
    if not os.path.exists(s._resolve_model("XAUUSD", "15min")):
        return
    w = _window()
    real = s.evaluate(w)
    if not real:
        return
    ctrl = build_strategy("ml_platform_random", {"minConfidence": 0.0, "seed": 1}).evaluate(w)
    assert len(ctrl) == 1, "control must fire on exactly the bars the model fires on"
    a, b = real[0], ctrl[0]
    assert b.strategy_name == "ml_platform_random"
    assert a.entry == b.entry
    assert abs(abs(a.entry - a.stop) - abs(b.entry - b.stop)) < Decimal("1e-9")
    rr_a = abs(a.target - a.entry) / abs(a.entry - a.stop)
    rr_b = abs(b.target - b.entry) / abs(b.entry - b.stop)
    assert abs(rr_a - rr_b) < Decimal("1e-9")


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
