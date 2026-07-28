"""Look-ahead-safe ICT geometry primitives.

These are pure functions over a **chronological** (oldest-first) list of
`IndicatorBar`s carrying full OHLC. They are the shared vocabulary every ICT
detector is built from, and they translate the codeable rules in
``docs/research/ict-concepts.md`` §3.

Non-negotiable discipline (concepts §3.11): *nothing is revealed before it could
be known.* A fractal swing at pivot index ``p`` is only confirmed ``k`` bars
later, so every `Swing` carries a ``confirm_index`` (= ``p + k``) and callers
filter on it. FVGs and order blocks reference only candles that have closed.
There is no repainting: a detector deciding on bar ``i`` sees exactly what a live
trader watching bar ``i`` close would have seen.

All prices are `Decimal` to match the rest of the engine (no float drift into
entry/stop/target).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..base import IndicatorBar

ZERO = Decimal("0")
TWO = Decimal("2")


def has_ohlc(bar: IndicatorBar) -> bool:
    """True only if open/high/low/close are all present (multi-bar detectors need them)."""
    return (
        bar.open is not None
        and bar.high is not None
        and bar.low is not None
        and bar.close is not None
    )


def window_has_ohlc(bars: list[IndicatorBar]) -> bool:
    return bool(bars) and all(has_ohlc(b) for b in bars)


# --------------------------------------------------------------------------- #
# Swing points & market structure (concepts §3.1)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Swing:
    index: int           # pivot bar index (chronological)
    confirm_index: int   # earliest bar by which this swing is known (index + k)
    timestamp: datetime
    price: Decimal
    kind: str            # "high" | "low"


def find_swings(bars: list[IndicatorBar], k: int = 2) -> list[Swing]:
    """Confirmed fractal swing highs/lows.

    A swing high at ``i`` is a strict local max of ``high`` over ``[i-k, i+k]``
    (strict, so flat/equal tops are NOT swings — those are liquidity pools,
    handled separately). Only pivots with ``k`` bars to their right are returned,
    so a swing never appears before its confirmation bar. Chronological order.
    """
    n = len(bars)
    out: list[Swing] = []
    for i in range(k, n - k):
        hi, lo = bars[i].high, bars[i].low
        assert hi is not None and lo is not None
        neigh = [j for j in range(i - k, i + k + 1) if j != i]
        if all(hi > bars[j].high for j in neigh):  # type: ignore[operator]
            out.append(Swing(i, i + k, bars[i].timestamp, hi, "high"))
        if all(lo < bars[j].low for j in neigh):  # type: ignore[operator]
            out.append(Swing(i, i + k, bars[i].timestamp, lo, "low"))
    return out


def last_swing(
    swings: list[Swing], kind: str, *, before_index: int, known_by: int
) -> Swing | None:
    """Most recent swing of ``kind`` strictly before ``before_index`` that is
    already confirmed as of bar ``known_by``. None if there isn't one yet."""
    found: Swing | None = None
    for s in swings:
        if s.kind == kind and s.index < before_index and s.confirm_index <= known_by:
            found = s  # swings are chronological, so the last match is the most recent
    return found


def nearest_liquidity_above(
    swings: list[Swing], price: Decimal, *, known_by: int
) -> Swing | None:
    """Closest confirmed swing high above ``price`` (buy-side liquidity / draw)."""
    cands = [
        s
        for s in swings
        if s.kind == "high" and s.confirm_index <= known_by and s.price > price
    ]
    return min(cands, key=lambda s: s.price) if cands else None


def nearest_liquidity_below(
    swings: list[Swing], price: Decimal, *, known_by: int
) -> Swing | None:
    """Closest confirmed swing low below ``price`` (sell-side liquidity / draw)."""
    cands = [
        s
        for s in swings
        if s.kind == "low" and s.confirm_index <= known_by and s.price < price
    ]
    return max(cands, key=lambda s: s.price) if cands else None


# --------------------------------------------------------------------------- #
# Displacement (concepts §3.4) — the gate for "institutional" moves
# --------------------------------------------------------------------------- #
def is_displacement(
    bar: IndicatorBar,
    *,
    body_mult: Decimal = Decimal("1.5"),
    body_ratio: Decimal = Decimal("0.5"),
) -> bool:
    """Fast, low-wick candle: body ≥ ``body_mult``·ATR and body/range ≥ ``body_ratio``.

    Uses the bar's own (causal) ATR reading; returns False if ATR is missing or
    the bar is a doji-ish range. This validates MSS and qualifies OB/FVG creation.
    """
    if bar.atr is None or bar.atr <= ZERO or not has_ohlc(bar):
        return False
    assert bar.open is not None and bar.high is not None and bar.low is not None and bar.close is not None
    body = abs(bar.close - bar.open)
    rng = bar.high - bar.low
    if rng <= ZERO:
        return False
    return body >= body_mult * bar.atr and (body / rng) >= body_ratio


def displacement_dir(bar: IndicatorBar) -> str:
    assert bar.close is not None and bar.open is not None
    return "LONG" if bar.close >= bar.open else "SHORT"


# --------------------------------------------------------------------------- #
# Fair Value Gap (concepts §3.5)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class FVG:
    index: int          # middle (displacement) candle index
    timestamp: datetime
    low: Decimal        # gap bottom
    high: Decimal       # gap top
    direction: str      # "LONG" (bullish gap) | "SHORT" (bearish gap)

    @property
    def ce(self) -> Decimal:
        """Consequent encroachment — the 50% reaction level ICT uses."""
        return (self.low + self.high) / TWO


def find_fvgs(bars: list[IndicatorBar], *, require_displacement: bool = True) -> list[FVG]:
    """Three-candle imbalances. Bullish at ``i``: ``low[i+1] > high[i-1]`` →
    gap ``(high[i-1], low[i+1])``; bearish is the mirror. Only middles whose
    next candle has closed are returned (so each gap is confirmed). When
    ``require_displacement`` the middle candle must itself be a displacement
    candle (the move that *created* the imbalance)."""
    out: list[FVG] = []
    for i in range(1, len(bars) - 1):
        prev, mid, nxt = bars[i - 1], bars[i], bars[i + 1]
        if not (has_ohlc(prev) and has_ohlc(mid) and has_ohlc(nxt)):
            continue
        if require_displacement and not is_displacement(mid):
            continue
        assert prev.high is not None and prev.low is not None
        assert nxt.high is not None and nxt.low is not None
        if nxt.low > prev.high:  # bullish FVG
            out.append(FVG(i, mid.timestamp, prev.high, nxt.low, "LONG"))
        elif nxt.high < prev.low:  # bearish FVG
            out.append(FVG(i, mid.timestamp, nxt.high, prev.low, "SHORT"))
    return out


def fvg_unmitigated_until(bars: list[IndicatorBar], fvg: FVG, end_index: int) -> bool:
    """True if, between the bar after formation and ``end_index`` (exclusive),
    price never fully traversed the gap. A bullish gap is mitigated once a bar's
    low trades below its bottom; a bearish gap once a bar's high trades above its
    top. (The retrace bar at ``end_index`` itself is the one we want to act on,
    so it is excluded from this check.)"""
    for j in range(fvg.index + 1, end_index):
        b = bars[j]
        if b.low is None or b.high is None:
            continue
        if fvg.direction == "LONG" and b.low <= fvg.low:
            return False
        if fvg.direction == "SHORT" and b.high >= fvg.high:
            return False
    return True


# --------------------------------------------------------------------------- #
# Order Block (concepts §3.3)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class OrderBlock:
    index: int          # the OB candle index
    timestamp: datetime
    low: Decimal
    high: Decimal
    direction: str      # "LONG" (bullish OB) | "SHORT" (bearish OB)
    bos_index: int      # the bar whose close broke structure, validating the OB

    @property
    def proximal(self) -> Decimal:
        """The edge price reaches first on a retrace (top for longs, bottom for shorts)."""
        return self.high if self.direction == "LONG" else self.low

    @property
    def distal(self) -> Decimal:
        return self.low if self.direction == "LONG" else self.high


def find_order_blocks(
    bars: list[IndicatorBar], swings: list[Swing], *, max_disp_bars: int = 3
) -> list[OrderBlock]:
    """Bullish OB = the last down-close candle immediately before a displacement
    up-leg that breaks the prior swing high (BOS_up) within ``max_disp_bars``
    candles; bearish is the mirror. The validating BOS must be a real break of a
    *confirmed* swing, so this is look-ahead safe — the OB only exists once the
    break has happened."""
    out: list[OrderBlock] = []
    n = len(bars)
    for c in range(n):
        ob = bars[c]
        if not has_ohlc(ob):
            continue
        assert ob.open is not None and ob.close is not None and ob.high is not None and ob.low is not None
        down_close = ob.close < ob.open
        up_close = ob.close > ob.open
        # scan the short leg that follows for a displacement break of structure
        for d in range(c + 1, min(c + 1 + max_disp_bars, n)):
            mover = bars[d]
            if not is_displacement(mover):
                continue
            ref_high = last_swing(swings, "high", before_index=c, known_by=d)
            ref_low = last_swing(swings, "low", before_index=c, known_by=d)
            assert mover.close is not None
            if down_close and ref_high is not None and mover.close > ref_high.price:
                out.append(OrderBlock(c, ob.timestamp, ob.low, ob.high, "LONG", d))
                break
            if up_close and ref_low is not None and mover.close < ref_low.price:
                out.append(OrderBlock(c, ob.timestamp, ob.low, ob.high, "SHORT", d))
                break
    return out


def ob_unmitigated_until(bars: list[IndicatorBar], ob: OrderBlock, end_index: int) -> bool:
    """True if price has not closed beyond the OB's far (distal) side between its
    validating break and ``end_index`` (exclusive) — i.e. the block is still
    live. A close beyond the far side invalidates it (concepts §3.3)."""
    for j in range(ob.bos_index + 1, end_index):
        b = bars[j]
        if b.close is None:
            continue
        if ob.direction == "LONG" and b.close < ob.low:
            return False
        if ob.direction == "SHORT" and b.close > ob.high:
            return False
    return True


# --------------------------------------------------------------------------- #
# Liquidity sweep (concepts §3.2) & market-structure shift (§3.1)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Sweep:
    index: int          # the bar that did the sweeping
    timestamp: datetime
    level: Decimal      # the swept liquidity level (the raided swing price)
    extreme: Decimal    # the sweep wick extreme (low for SSL raid, high for BSL raid)
    side: str           # "SSL" (raided sell-side) | "BSL" (raided buy-side)


def detect_sweep(
    bars: list[IndicatorBar],
    swings: list[Swing],
    *,
    end_index: int,
    side: str,
    lookback: int = 5,
) -> Sweep | None:
    """Most recent stop-raid in the last ``lookback`` bars up to ``end_index``.

    SSL raid (bullish setup): a bar trades **below** a prior confirmed swing low
    but **closes back above** it (wick takes the level, body rejects). BSL raid
    is the mirror above a swing high. Returns the most recent qualifying sweep.
    """
    start = max(0, end_index - lookback + 1)
    found: Sweep | None = None
    for j in range(start, end_index + 1):
        b = bars[j]
        if not has_ohlc(b):
            continue
        assert b.low is not None and b.high is not None and b.close is not None
        if side == "SSL":
            sl = last_swing(swings, "low", before_index=j, known_by=j)
            if sl is not None and b.low < sl.price and b.close > sl.price:
                found = Sweep(j, b.timestamp, sl.price, b.low, "SSL")
        elif side == "BSL":
            sh = last_swing(swings, "high", before_index=j, known_by=j)
            if sh is not None and b.high > sh.price and b.close < sh.price:
                found = Sweep(j, b.timestamp, sh.price, b.high, "BSL")
    return found


def mss_break(
    bars: list[IndicatorBar], swings: list[Swing], *, at_index: int, direction: str
) -> Swing | None:
    """Return the swing broken by a displacement-validated Market-Structure-Shift
    at ``at_index``, or None. Up-MSS: the bar is a displacement up AND its close
    breaks the last confirmed swing high. Mirror for down."""
    bar = bars[at_index]
    if not is_displacement(bar):
        return None
    assert bar.close is not None
    if direction == "LONG":
        ref = last_swing(swings, "high", before_index=at_index, known_by=at_index)
        if ref is not None and bar.close > ref.price:
            return ref
    else:
        ref = last_swing(swings, "low", before_index=at_index, known_by=at_index)
        if ref is not None and bar.close < ref.price:
            return ref
    return None


# --------------------------------------------------------------------------- #
# Bias (concepts §2 "daily bias") — a light HTF directional gate
# --------------------------------------------------------------------------- #
def ema_bias(bar: IndicatorBar) -> str | None:
    """Coarse trend lean from the EMA stack on the decision bar: LONG if
    EMA50 > EMA200, SHORT if below, None when the EMAs aren't warmed up (in which
    case the caller should not hard-gate on bias)."""
    if bar.ema50 is None or bar.ema200 is None:
        return None
    if bar.ema50 > bar.ema200:
        return "LONG"
    if bar.ema50 < bar.ema200:
        return "SHORT"
    return None
