"""Look-ahead and determinism tests for the shared ML feature builder.

Pure logic — no DB. Runnable under pytest, or directly:
``python tests/test_ml_features.py``.

The ported xaubot model failed partly because training and inference built
features differently. These tests pin the two properties that make a single
shared builder safe to trust:

* a feature row for bar *t* cannot be influenced by anything after *t*;
* the same window always produces the same row, and the window LENGTH is part
  of the contract (ICT structure is found by scanning the window, so a longer
  window can legitimately surface an older swing/order block — training and
  live must therefore pass exactly `LOOKBACK` bars).

The second half of the file covers `strategies/ml_xau.py`, which is a different
kind of thing: a frozen reproduction of a compiled ONNX binary's inputs rather
than a builder we own. There the tests pin the contract in place instead of
proving properties about it.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from strategies import ml_xau as MX  # noqa: E402
from strategies.base import IndicatorBar  # noqa: E402
from strategies.ict import primitives as P  # noqa: E402
from training.features import (  # noqa: E402
    FEATURE_NAMES,
    LOOKBACK,
    N_FEATURES,
    build_feature_row,
)

START = datetime(2026, 1, 5, 0, 0)


def _series(n: int, seed: int = 7) -> list[IndicatorBar]:
    """Synthetic but structurally rich bars — a random walk with enough range to
    produce swings, gaps and order blocks."""
    rng = np.random.default_rng(seed)
    price = 4000.0
    bars: list[IndicatorBar] = []
    closes: list[float] = []
    for i in range(n):
        drift = rng.normal(0, 3.0)
        price = max(100.0, price + drift)
        rng_bar = abs(rng.normal(0, 2.0)) + 0.5
        o = price + rng.normal(0, 0.5)
        c = price
        h = max(o, c) + rng_bar
        low = min(o, c) - rng_bar
        closes.append(c)
        window = closes[-14:]
        atr = max(0.5, float(np.std(window)) + 1.0)
        bars.append(
            IndicatorBar(
                timestamp=START + timedelta(minutes=15 * i),
                close=Decimal(str(round(c, 3))),
                open=Decimal(str(round(o, 3))),
                high=Decimal(str(round(h, 3))),
                low=Decimal(str(round(low, 3))),
                rsi=Decimal("55"),
                ema20=Decimal(str(round(float(np.mean(closes[-20:])), 3))),
                ema50=Decimal(str(round(float(np.mean(closes[-50:])), 3))),
                ema200=Decimal(str(round(float(np.mean(closes[-200:])), 3))),
                atr=Decimal(str(round(atr, 3))),
                bb_lower=Decimal(str(round(c - 2 * atr, 3))),
                bb_upper=Decimal(str(round(c + 2 * atr, 3))),
                bb_pctb=Decimal("0.5"),
                adx=Decimal("22"),
            )
        )
    return bars


def test_row_shape_and_finiteness() -> None:
    bars = _series(LOOKBACK + 50)
    row = build_feature_row(bars[-LOOKBACK:], timeframe="15min")
    assert row is not None
    assert row.shape == (N_FEATURES,), row.shape
    assert len(FEATURE_NAMES) == N_FEATURES
    assert np.isfinite(row).all(), "feature row must never contain NaN/inf"


def test_short_window_returns_none() -> None:
    bars = _series(LOOKBACK - 1)
    assert build_feature_row(bars, timeframe="15min") is None


def test_deterministic() -> None:
    bars = _series(LOOKBACK + 10)
    w = bars[-LOOKBACK:]
    a = build_feature_row(w, timeframe="15min")
    b = build_feature_row(w, timeframe="15min")
    assert a is not None and b is not None
    assert np.array_equal(a, b)


def test_future_bars_cannot_change_the_row() -> None:
    """The core look-ahead guard.

    Build the row for a decision bar, then replace every subsequent bar with a
    violent spike and rebuild for the SAME decision bar. Identical output means
    no feature reaches past its window.
    """
    bars = _series(LOOKBACK + 80)
    cut = LOOKBACK  # decision bar index
    window = bars[cut - LOOKBACK + 1 : cut + 1]
    before = build_feature_row(window, timeframe="15min")

    poisoned = list(bars)
    for j in range(cut + 1, len(poisoned)):
        b = poisoned[j]
        poisoned[j] = IndicatorBar(
            timestamp=b.timestamp,
            close=Decimal("99999"), open=Decimal("99999"),
            high=Decimal("99999"), low=Decimal("99999"),
            rsi=Decimal("99"), ema20=Decimal("99999"), ema50=Decimal("99999"),
            ema200=Decimal("99999"), atr=Decimal("500"),
            bb_lower=Decimal("0"), bb_upper=Decimal("99999"),
            bb_pctb=Decimal("1"), adx=Decimal("99"),
        )
    after = build_feature_row(
        poisoned[cut - LOOKBACK + 1 : cut + 1], timeframe="15min"
    )
    assert before is not None and after is not None
    assert np.array_equal(before, after), "future bars leaked into the feature row"


def test_swings_respect_confirmation_lag() -> None:
    """No swing may be usable before its `confirm_index`.

    `find_swings(k=2)` needs two bars to the right of a pivot, so the last two
    bars of any window can never yield a confirmed swing. This is the property
    the ICT features rely on for causality.
    """
    bars = _series(LOOKBACK)
    li = len(bars) - 1
    swings = P.find_swings(bars, k=2)
    assert swings, "fixture should produce some swings"
    for s in swings:
        assert s.confirm_index <= li, "swing confirmed after the decision bar"
        assert s.index <= li - 2, "swing pivot too close to the decision bar to be confirmed"


def test_features_are_scale_free() -> None:
    """Doubling the price level must not blow up the features.

    This is the xaubot failure encoded as a test: it fed raw `ema_10/20/50`, so
    when gold moved from ~$1,900 to ~$4,000 every tree split saturated. Ratio and
    ATR-normalised features should be near-invariant to a pure rescale.
    """
    base = _series(LOOKBACK + 5)

    def rescale(b: IndicatorBar, k: Decimal) -> IndicatorBar:
        m = lambda x: None if x is None else x * k  # noqa: E731
        return IndicatorBar(
            timestamp=b.timestamp, close=m(b.close), open=m(b.open),
            high=m(b.high), low=m(b.low), rsi=b.rsi,
            ema20=m(b.ema20), ema50=m(b.ema50), ema200=m(b.ema200),
            atr=m(b.atr), bb_lower=m(b.bb_lower), bb_upper=m(b.bb_upper),
            bb_pctb=b.bb_pctb, adx=b.adx,
        )

    a = build_feature_row(base[-LOOKBACK:], timeframe="15min")
    scaled = [rescale(b, Decimal("2")) for b in base]
    b = build_feature_row(scaled[-LOOKBACK:], timeframe="15min")
    assert a is not None and b is not None

    # atr_pct is the one intentionally price-relative ratio that is invariant;
    # everything else is either ATR-normalised or a pure ratio/flag.
    worst_name, worst = "", 0.0
    for i, name in enumerate(FEATURE_NAMES):
        d = abs(float(a[i]) - float(b[i]))
        if d > worst:
            worst_name, worst = name, d
    assert worst < 1e-3, f"feature '{worst_name}' moved {worst:.3e} under a 2x price rescale"


# --------------------------------------------------------------------------- #
# ml_xau — frozen legacy contract
#
# ml_xau reproduces a compiled ONNX binary's inputs. LightGBM binds by position,
# so a reorder or a formula tweak feeds the model different numbers with no
# error anywhere. These tests exist so that breaks the build instead.
# --------------------------------------------------------------------------- #

def test_ml_xau_contract_frozen() -> None:
    """The 26 inputs, in order, exactly as the ONNX binary expects them.

    Spelled out literally rather than derived — a test that recomputes the
    contract from the module under test cannot detect the module changing.
    """
    assert MX.FEATURE_ORDER == (
        "body", "body_abs", "candle_range", "close_position",
        "return_1", "return_5", "return_15", "return_60",
        "tr", "atr_14", "rsi_14",
        "ema_10", "ema_20", "ema_50",
        "hour_sin", "hour_cos",
        "mtf_0", "mtf_1", "mtf_2", "mtf_3", "mtf_4",
        "mtf_5", "mtf_6", "mtf_7", "mtf_8", "mtf_9",
    )
    assert len(MX.FEATURE_ORDER) == 26
    assert (MX.SHORT, MX.HOLD, MX.LONG) == (0, 1, 2)


def _ohlc(n: int, seed: int = 3):
    rng = np.random.default_rng(seed)
    price = 2000.0
    o = np.empty(n); h = np.empty(n); low = np.empty(n); c = np.empty(n)
    for i in range(n):
        price = max(50.0, price + rng.normal(0, 3.0))
        op, cl = price + rng.normal(0, 0.5), price
        span = abs(rng.normal(0, 2.0)) + 0.5
        o[i], c[i] = op, cl
        h[i], low[i] = max(op, cl) + span, min(op, cl) - span
    ts = np.arange("2026-01-01T00:00", n, dtype="datetime64[m]")
    return ts, o, h, low, c


def test_ml_xau_mtf_inputs_are_dead() -> None:
    """10 of the 26 inputs are hard-coded 0.0 — placeholders for a Transformer
    upstream never shipped. Pinned so nobody 'fixes' them into real values and
    silently changes what the frozen binary is fed."""
    feats = MX.build_features(*_ohlc(MX.MlXau.lookback))
    for i, name in enumerate(MX.FEATURE_ORDER):
        if name.startswith("mtf_"):
            assert (feats[:, i] == 0.0).all(), f"{name} is no longer a zero placeholder"


def test_ml_xau_ema_guard_raises_instead_of_zeroing() -> None:
    """Past ~3,550 bars the vectorised `_ema` used to return a silent 0.0.

    Loud failure is the whole point: a batch caller getting zeroed EMAs back
    would see plausible-looking features and a model quietly scoring garbage.
    """
    safe = MX._ema(np.full(100, 2000.0), 10)
    assert np.isfinite(safe).all() and safe[-1] > 1000.0

    try:
        MX._ema(np.full(5000, 2000.0), 10)
    except ValueError as exc:
        assert "silently returns zeros" in str(exc)
    else:
        raise AssertionError("_ema must raise past its safe length, not return zeros")


def test_ml_xau_window_length_changes_the_ema_features() -> None:
    """Documents the train/live skew we cannot close from this side.

    Upstream trained on full-series pandas `ewm`; we infer over a 100-bar
    window. The EMAs are path-dependent, so those two disagree. This test pins
    the fact that they disagree — if it ever starts passing with equality, the
    feature semantics changed and the frozen binary is being fed something new.
    """
    n = 3000  # under the _ema guard, well over the 100-bar live window
    ts, o, h, low, c = _ohlc(n)
    full = MX.build_features(ts, o, h, low, c)[-1]
    win = MX.build_features(ts[-100:], o[-100:], h[-100:], low[-100:], c[-100:])[-1]

    idx = {name: i for i, name in enumerate(MX.FEATURE_ORDER)}
    # Path-independent features must agree exactly...
    for name in ("body", "candle_range", "close_position", "tr", "atr_14",
                 "rsi_14", "return_1", "return_60", "hour_sin"):
        assert abs(float(full[idx[name]]) - float(win[idx[name]])) < 1e-6, name
    # ...while ema_50 carries real warmup error at a 100-bar window.
    assert abs(float(full[idx["ema_50"]]) - float(win[idx["ema_50"]])) > 1e-3, (
        "ema_50 no longer shows window sensitivity — feature semantics changed"
    )


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
