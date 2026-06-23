"""Unit tests for the Phase 4 strategy modules, on hand-built bar fixtures.

Mirrors the entry/exit-assertion style of the TS riskEngine/paperTrading tests.
Runnable under pytest, or directly: ``python tests/test_strategies.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import BarWindow, IndicatorBar, build_strategy  # noqa: E402

TS = datetime(2026, 6, 23, 12, 0, 0)


def _window(*bars: IndicatorBar, symbol: str = "EURUSD", timeframe: str = "60min") -> BarWindow:
    return BarWindow(symbol=symbol, timeframe=timeframe, bars=list(bars))


def test_trend_ema_emits_long_on_pullback() -> None:
    strat = build_strategy("trend_ema", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("100"),
        ema20=Decimal("10"), ema50=Decimal("9"), rsi=Decimal("48"), atr=Decimal("6"),
    )
    out = strat.evaluate(_window(bar))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.strategy_name == "trend_ema"
    assert c.entry == Decimal("100")
    assert c.stop == Decimal("100") - Decimal("1.5") * Decimal("6")  # 91
    assert c.target == Decimal("100") + Decimal("3") * Decimal("6")  # 118
    assert c.cooldown_ms == 3_600_000
    assert c.client_id is None


def test_trend_ema_skips_when_rsi_out_of_band() -> None:
    strat = build_strategy("trend_ema", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("100"),
        ema20=Decimal("10"), ema50=Decimal("9"), rsi=Decimal("70"), atr=Decimal("6"),
    )
    assert strat.evaluate(_window(bar)) == []


def test_trend_ema_skips_when_trend_not_bullish() -> None:
    strat = build_strategy("trend_ema", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("100"),
        ema20=Decimal("8"), ema50=Decimal("9"), rsi=Decimal("48"), atr=Decimal("6"),
    )
    assert strat.evaluate(_window(bar)) == []


def test_meanrev_emits_long_when_oversold_in_uptrend() -> None:
    strat = build_strategy("meanrev_rsi", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("200"),
        ema200=Decimal("150"), rsi=Decimal("25"), atr=Decimal("4"),
    )
    out = strat.evaluate(_window(bar))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "LONG"
    assert c.stop == Decimal("200") - Decimal("1.5") * Decimal("4")  # 194
    assert c.target == Decimal("200") + Decimal("3") * Decimal("4")  # 212
    assert c.client_id is not None and len(c.client_id) == 24


def test_meanrev_emits_short_when_overbought_in_downtrend() -> None:
    strat = build_strategy("meanrev_rsi", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("100"),
        ema200=Decimal("150"), rsi=Decimal("80"), atr=Decimal("2"),
    )
    out = strat.evaluate(_window(bar))
    assert len(out) == 1
    c = out[0]
    assert c.direction == "SHORT"
    assert c.stop == Decimal("100") + Decimal("1.5") * Decimal("2")  # 103
    assert c.target == Decimal("100") - Decimal("3") * Decimal("2")  # 94


def test_meanrev_skips_neutral_rsi() -> None:
    strat = build_strategy("meanrev_rsi", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("100"),
        ema200=Decimal("150"), rsi=Decimal("50"), atr=Decimal("2"),
    )
    assert strat.evaluate(_window(bar)) == []


def test_meanrev_client_id_is_deterministic() -> None:
    strat = build_strategy("meanrev_rsi", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("200"),
        ema200=Decimal("150"), rsi=Decimal("25"), atr=Decimal("4"),
    )
    a = strat.evaluate(_window(bar))[0]
    b = strat.evaluate(_window(bar))[0]
    assert a.client_id == b.client_id  # idempotent across runs


def test_candidate_payload_is_camelcase_json() -> None:
    strat = build_strategy("trend_ema", None)
    bar = IndicatorBar(
        timestamp=TS, close=Decimal("100"),
        ema20=Decimal("10"), ema50=Decimal("9"), rsi=Decimal("48"), atr=Decimal("6"),
    )
    payload = strat.evaluate(_window(bar))[0].to_payload()
    assert payload["strategyName"] == "trend_ema"
    assert payload["direction"] == "LONG"
    assert isinstance(payload["entryPrice"], float) and payload["entryPrice"] == 100.0
    assert payload["cooldownMs"] == 3_600_000
    assert payload["aiMinScore"] == 70
    assert "clientId" not in payload  # trend_ema relies on cooldown, not per-bar id


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(fns)} passed")
