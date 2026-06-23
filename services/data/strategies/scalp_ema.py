"""scalp_ema — intraday momentum scalp with a fixed pip target.

Designed to produce roughly one actionable setup per day per symbol on a low
timeframe (5min), with small pip-based targets rather than ATR-based stops:

  LONG  when EMA20 > EMA50 and close > EMA20 and RSI in a momentum band.
  SHORT when EMA20 < EMA50 and close < EMA20 and RSI in the mirror band.

Targets are fixed in pips: TP = 10 pips, SL = 5 pips (risk:reward 1:2, which
clears the risk engine's MIN_RR). Pip size is per-symbol.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .base import RANGING, TRENDING, VOLATILE, BarWindow, SignalCandidate

# One pip per symbol (price units).
_DEFAULT_PIP: dict[str, float] = {
    "XAUUSD": 0.1,
    "EURUSD": 0.0001,
    "BTCUSD": 1.0,
}


class ScalpEma:
    name = "scalp_ema"
    regimes = {TRENDING, RANGING, VOLATILE}  # intraday; runs in any regime

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # 100-pip target / 50-pip stop. At 0.01 lot on XAUUSD (1 oz, pip = $0.10)
        # that's a ~$10 take-profit for ~$5 risk (1:2).
        self.tp_pips = Decimal(str(p.get("tpPips", 100)))
        self.sl_pips = Decimal(str(p.get("slPips", 50)))
        self.rsi_long_min = Decimal(str(p.get("rsiLongMin", 45)))
        self.rsi_long_max = Decimal(str(p.get("rsiLongMax", 68)))
        self.rsi_short_min = Decimal(str(p.get("rsiShortMin", 32)))
        self.rsi_short_max = Decimal(str(p.get("rsiShortMax", 55)))
        self.cooldown_ms = int(p.get("cooldownMs", 4 * 60 * 60 * 1000))
        self.ai_min_score = int(p.get("aiMinScore", 45))
        self._pip = {**_DEFAULT_PIP, **(p.get("pip") or {})}

    def _pip_size(self, symbol: str) -> Decimal:
        return Decimal(str(self._pip.get(symbol, 0.0001)))

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bar = window.latest
        if bar is None or None in (bar.ema20, bar.ema50, bar.rsi):
            return []
        ema20, ema50, rsi, close = bar.ema20, bar.ema50, bar.rsi, bar.close
        assert ema20 is not None and ema50 is not None and rsi is not None

        pip = self._pip_size(window.symbol)
        long_ok = ema20 > ema50 and close > ema20 and self.rsi_long_min <= rsi <= self.rsi_long_max
        short_ok = ema20 < ema50 and close < ema20 and self.rsi_short_min <= rsi <= self.rsi_short_max

        if long_ok:
            direction = "LONG"
            stop = close - self.sl_pips * pip
            target = close + self.tp_pips * pip
        elif short_ok:
            direction = "SHORT"
            stop = close + self.sl_pips * pip
            target = close - self.tp_pips * pip
        else:
            return []

        reasoning = (
            f"Intraday {direction} scalp: EMA20 {ema20} {'>' if direction == 'LONG' else '<'} "
            f"EMA50 {ema50} and price {'above' if direction == 'LONG' else 'below'} EMA20 "
            f"(momentum), RSI {rsi}. Fixed target {self.tp_pips} pips, stop {self.sl_pips} pips "
            f"(1:2 R:R)."
        )
        return [
            SignalCandidate(
                strategy_name=self.name,
                symbol=window.symbol,
                timeframe=window.timeframe,
                direction=direction,
                entry=close,
                stop=stop,
                target=target,
                confidence=60,
                reasoning=reasoning,
                cooldown_ms=self.cooldown_ms,
                ai_min_score=self.ai_min_score,
            )
        ]
