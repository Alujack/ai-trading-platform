"""scalp_sniper — momentum-burst continuation scalp (the aggressive-scalper
skill's *stacking* entry, survivable variant).

Where ``scalp_vwap`` waits for a pullback that retests VWAP, this enters DURING
a confirmed burst — the mode used when momentum is running and the playbook says
"keep entering as long as the trend holds". It encodes the playbook filters that
separated the winning stacks from the bleed in live testing, none of which exist
in scalp_vwap:

  - **Burst** — 3+ consecutive closed bars in the same direction (the "3+
    consecutive 1min candles" rule). Two-sided; never biased.
  - **Range expansion** — the trigger bar's range must exceed EACH of the prior
    3 bars' ranges: expanding range = real thrust; contracting = the move is
    dying and you're buying the top.
  - **Blow-off veto** — if the PRIOR bar was already a huge-range candle after a
    run, expansion is exhaustion, not thrust. Skip (the "don't chase the
    extended end" rule in candle form).
  - **Wick-absorption veto** — 2+ recent bars with significant opposing wicks
    (sellers absorbing above for a LONG / buyers absorbing below for a SHORT)
    block the entry. This is the s11-long-3 lesson: close on the wick, don't
    trade into it.
  - **Extension veto** — after a long run without a pullback, do not enter (the
    38–50%-pullback rule, expressed as max run length in ATR units).
  - **VWAP bias + slope** — price on the trend side of VWAP AND VWAP itself
    sloping that way. Counter-VWAP entries are fighting the controlling side.
  - **Breakout-or-distance location** — entering right under the window high
    (for a LONG) is only allowed when the trigger bar CLOSED beyond it (a clean
    break); otherwise demand room to the extreme (the "5pt from session
    high/low" rule in ATR units).

Stops are structural, not fill-based: beyond the burst's own 3-bar extreme plus
an ATR buffer, floored at a minimum ATR distance (the "minimum 2.5pt SL, never
tighter" rule). TP = 2R, clearing the risk engine's MIN_RR.

It declares ``regimes = {TRENDING}`` — the regime gate keeps it out of chop and
news, the regimes where every variant of this strategy bled. Position stacking
(multiple concurrent 0.01 lots) is an EXECUTION-layer concern and is not done
here: this module only decides whether THIS bar is a valid add.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base import TRENDING, BarWindow, IndicatorBar, SignalCandidate
from .scalp_vwap import _vwap


def _signal_id(symbol: str, timeframe: str, direction: str, bar_ts: datetime) -> str:
    """Deterministic per-bar id so re-emitting the same trigger bar is idempotent."""
    key = f"scalp_sniper|{symbol}|{timeframe}|{direction}|{bar_ts.isoformat()}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


def _rng(b: IndicatorBar) -> Decimal | None:
    if b.high is None or b.low is None:
        return None
    return b.high - b.low


class ScalpSniper:
    name = "scalp_sniper"
    regimes = {TRENDING}  # chop and news kill this entry; the runner gates them out
    lookback = 120  # anchor an intraday VWAP + read the run's structure

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        # Burst definition.
        self.burst_bars = int(p.get("burstBars", 3))            # consecutive same-direction closes
        self.expansion_lookback = int(p.get("expansionLookback", 3))
        # Vetos (ATR units so they scale across symbols/timeframes).
        self.blowoff_mult = Decimal(str(p.get("blowoffMult", 2.5)))    # prior bar range vs avg = exhaustion
        self.wick_veto_atr = Decimal(str(p.get("wickVetoAtr", 0.5)))   # ~1.5pt on M1 gold
        self.wick_window = int(p.get("wickWindow", 4))
        self.wick_veto_count = int(p.get("wickVetoCount", 2))
        self.max_run_atr = Decimal(str(p.get("maxRunAtr", 4.0)))       # extension: no entry past this run
        self.extreme_band_atr = Decimal(str(p.get("extremeBandAtr", 0.75)))
        self.vwap_slope_bars = int(p.get("vwapSlopeBars", 5))
        # Volume.
        self.vol_lookback = int(p.get("volLookback", 20))
        self.vol_min_ratio = Decimal(str(p.get("volMinRatio", 1.0)))   # burst must carry volume
        # Structural stop frame.
        self.sl_buffer_atr = Decimal(str(p.get("slBufferAtr", 0.5)))   # beyond the burst extreme
        self.sl_min_atr = Decimal(str(p.get("slMinAtr", 1.0)))         # "minimum 2.5pt SL" in ATR form
        self.rr = Decimal(str(p.get("rr", 2.0)))                       # clears MIN_RR
        self.cooldown_ms = int(p.get("cooldownMs", 3 * 60 * 1000))     # re-entry cadence while trending
        self.ai_min_score = int(p.get("aiMinScore", 60))
        # Liquidity session gate (playbook: thin off-session spread eats the
        # scalp target — skip). NY-local hours; default London open → NY morning.
        self.session_gate = bool(p.get("sessionGate", True))
        self.session_start_ny = int(p.get("sessionStartNy", 2))
        self.session_end_ny = int(p.get("sessionEndNy", 10))

    # -- factor helpers ---------------------------------------------------

    def _in_session(self, ts: datetime) -> bool:
        """Is this bar inside the liquid window (NY-local hours)?"""
        from datetime import timezone
        from zoneinfo import ZoneInfo

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ny_hour = ts.astimezone(ZoneInfo("America/New_York")).hour
        return self.session_start_ny <= ny_hour < self.session_end_ny

    def _burst_direction(self, bars: list[IndicatorBar]) -> str | None:
        """All of the last `burst_bars` closed in one direction, or no trade."""
        dirs: list[str] = []
        for b in bars[: self.burst_bars]:
            if b.open is None:
                return None
            if b.close > b.open:
                dirs.append("LONG")
            elif b.close < b.open:
                dirs.append("SHORT")
            else:
                return None
        return dirs[0] if len(set(dirs)) == 1 else None

    def _range_expanding(self, bars: list[IndicatorBar]) -> bool:
        """Trigger bar's range exceeds EACH of the prior `expansion_lookback` ranges."""
        trig = _rng(bars[0])
        if trig is None:
            return False
        for b in bars[1 : self.expansion_lookback + 1]:
            r = _rng(b)
            if r is None or trig <= r:
                return False
        return True

    def _prior_blowoff(self, bars: list[IndicatorBar]) -> bool:
        """Prior bar already a blow-off (huge range vs the bars before it)."""
        prior = _rng(bars[1])
        if prior is None:
            return False
        refs = [r for b in bars[2 : 2 + 5] if (r := _rng(b)) is not None and r > 0]
        if not refs:
            return False
        avg = sum(refs, Decimal("0")) / Decimal(len(refs))
        return avg > 0 and prior > self.blowoff_mult * avg

    def _wick_absorption(self, bars: list[IndicatorBar], atr: Decimal, direction: str) -> bool:
        """2+ recent bars show significant opposing wicks — the other side is
        absorbing at this zone; entering walks into the reversal."""
        threshold = self.wick_veto_atr * atr
        count = 0
        for b in bars[: self.wick_window]:
            if b.open is None or b.high is None or b.low is None:
                continue
            if direction == "LONG":
                wick = b.high - max(b.open, b.close)   # upper wick = sellers above
            else:
                wick = min(b.open, b.close) - b.low    # lower wick = buyers below
            if wick >= threshold:
                count += 1
        return count >= self.wick_veto_count

    def _volume_ratio(self, latest: IndicatorBar, bars: list[IndicatorBar]) -> Decimal | None:
        vols = [b.volume for b in bars[1 : 1 + self.vol_lookback] if b.volume is not None]
        if latest.volume is None or not vols:
            return None
        avg = sum(vols, Decimal("0")) / Decimal(len(vols))
        return latest.volume / avg if avg > 0 else None

    # -- main -------------------------------------------------------------

    def evaluate(self, window: BarWindow) -> list[SignalCandidate]:
        bars = window.bars  # most-recent first
        if len(bars) < max(self.burst_bars + self.expansion_lookback + 7, 20):
            return []

        latest = bars[0]
        if None in (latest.ema20, latest.ema50, latest.atr, latest.open, latest.high, latest.low):
            return []
        ema20, ema50, atr = latest.ema20, latest.ema50, latest.atr
        close = latest.close
        assert ema20 is not None and ema50 is not None and atr is not None
        if atr <= 0:
            return []

        # --- SESSION: only trade the liquid window (spread eats off-session scalps) ---
        if self.session_gate and not self._in_session(latest.timestamp):
            return []

        # --- BURST: 3+ consecutive same-direction closes ---
        direction = self._burst_direction(bars)
        if direction is None:
            return []

        # --- trend GATE: EMA stack must agree with the burst ---
        if direction == "LONG" and not ema20 > ema50:
            return []
        if direction == "SHORT" and not ema20 < ema50:
            return []

        # --- VWAP bias AND slope must agree (never fight the controlling side) ---
        vwap_now = _vwap(bars)
        vwap_prev = _vwap(bars[self.vwap_slope_bars :])
        if vwap_now is None or vwap_prev is None:
            return []
        if direction == "LONG" and not (close > vwap_now and vwap_now >= vwap_prev):
            return []
        if direction == "SHORT" and not (close < vwap_now and vwap_now <= vwap_prev):
            return []

        # --- ACCELERATION: range expansion, and the prior bar wasn't a blow-off ---
        if not self._range_expanding(bars):
            return []
        if self._prior_blowoff(bars):
            return []

        # --- WICK-ABSORPTION veto ---
        if self._wick_absorption(bars, atr, direction):
            return []

        # --- EXTENSION veto: don't chase a run that never pulled back ---
        run_window = bars[:10]
        if direction == "LONG":
            run_from = min((b.low for b in run_window if b.low is not None), default=close)
            if (close - run_from) > self.max_run_atr * atr:
                return []
        else:
            run_from = max((b.high for b in run_window if b.high is not None), default=close)
            if (run_from - close) > self.max_run_atr * atr:
                return []

        # --- LOCATION: clean break of the window extreme, or room to it ---
        prior_bars = bars[1:]
        if direction == "LONG":
            prior_high = max((b.high for b in prior_bars if b.high is not None), default=close)
            broke_out = close > prior_high
            if not broke_out and (prior_high - close) < self.extreme_band_atr * atr:
                return []
        else:
            prior_low = min((b.low for b in prior_bars if b.low is not None), default=close)
            broke_out = close < prior_low
            if not broke_out and (close - prior_low) < self.extreme_band_atr * atr:
                return []

        # --- VOLUME: the burst must carry volume ---
        vol_ratio = self._volume_ratio(latest, bars)
        if vol_ratio is not None and vol_ratio < self.vol_min_ratio:
            return []

        # --- structural stop beyond the burst extreme, floored at sl_min_atr ---
        burst = bars[: self.burst_bars]
        if direction == "LONG":
            structural = min((b.low for b in burst if b.low is not None), default=close)
            stop = min(structural - self.sl_buffer_atr * atr, close - self.sl_min_atr * atr)
            risk = close - stop
            target = close + self.rr * risk
        else:
            structural = max((b.high for b in burst if b.high is not None), default=close)
            stop = max(structural + self.sl_buffer_atr * atr, close + self.sl_min_atr * atr)
            risk = stop - close
            target = close - self.rr * risk
        if risk <= 0:
            return []

        # --- grade → confidence: breakout, volume, marubozu-ish trigger all add ---
        o, h, l = latest.open, latest.high, latest.low
        assert o is not None and h is not None and l is not None
        rng = h - l
        body_pct = (abs(close - o) / rng) if rng > 0 else Decimal("0")
        score = 55
        if broke_out:
            score += 12
        if vol_ratio is not None and vol_ratio >= Decimal("1.2"):
            score += 11
        if body_pct >= Decimal("0.8"):  # marubozu-like trigger = confirmed momentum
            score += 12
        confidence = max(50, min(90, score))
        grade = "A" if confidence >= 78 else "B" if confidence >= 64 else "C"

        reasoning = (
            f"[{grade}] {direction} scalp_sniper @ {close}: burst of {self.burst_bars}+ "
            f"same-direction bars with range expansion, EMA20 {ema20} "
            f"{'>' if direction == 'LONG' else '<'} EMA50 {ema50}, price "
            f"{'above' if direction == 'LONG' else 'below'} VWAP {vwap_now:.3f} with slope agreeing. "
            f"{'Clean break of window extreme. ' if broke_out else 'Room to window extreme. '}"
            f"No opposing wick absorption, run not extended, prior bar not a blow-off. "
            f"Volume {('n/a' if vol_ratio is None else f'{vol_ratio:.2f}x avg')}, trigger body "
            f"{body_pct:.0%}. Structural SL beyond burst extreme (risk {risk:.3f}), TP {self.rr}R. "
            f"Regime-gated to TRENDING only."
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
                confidence=confidence,
                reasoning=reasoning,
                client_id=_signal_id(window.symbol, window.timeframe, direction, latest.timestamp),
                cooldown_ms=self.cooldown_ms,
                ai_min_score=self.ai_min_score,
            )
        ]
