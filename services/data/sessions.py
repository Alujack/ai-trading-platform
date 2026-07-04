"""Trading session detection and session-level computation for XAU/USD.

Gold's intraday price action is heavily structured by the three global FX
sessions.  This module provides:

  - **Session classification** — which session a timestamp falls in.
  - **Asian range computation** — the high/low of the Asian session, used by
    the London Sweep and Asian Breakout strategies as entry anchors.
  - **Session overlap detection** — the London-NY overlap window is the
    highest-liquidity period and the primary scalping window.

All windows are defined in **America/New_York** time to stay consistent with
the ICT killzones module (`strategies/ict/killzones.py`).

Session definitions (NY local time):
  - Asian:           20:00 – 00:00  (previous day's NY evening → midnight)
  - London:          02:00 – 05:00  (early morning, sweeps Asian range)
  - London extended: 05:00 – 07:00  (continuation / consolidation)
  - NY AM:           07:00 – 10:00  (the primary directional window)
  - London-NY overlap: 07:00 – 10:00 (= NY AM; both desks active)
  - NY PM:           10:00 – 14:00  (afternoon session, lower volatility)

Usage::

    from sessions import SessionInfo, classify_session, compute_asian_range

    info = classify_session(candle_timestamp)
    asian = await compute_asian_range(pool, "XAUUSD", reference_date)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from typing import Sequence
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# Session windows in NY local time (start inclusive, end exclusive).
SESSION_WINDOWS: dict[str, tuple[time, time]] = {
    "asian":           (time(20, 0), time(23, 59, 59)),  # 20:00–00:00 (wraps midnight)
    "asian_late":      (time(0, 0), time(2, 0)),          # 00:00–02:00 (late Asian/pre-London)
    "london":          (time(2, 0), time(5, 0)),          # 02:00–05:00 (primary London)
    "london_extended": (time(5, 0), time(7, 0)),          # 05:00–07:00 (continuation)
    "ny_am":           (time(7, 0), time(10, 0)),         # 07:00–10:00 (NY morning + overlap)
    "ny_pm":           (time(10, 0), time(14, 0)),        # 10:00–14:00 (NY afternoon)
}

# The overlap window where both London and NY desks are active.
# This is the highest-liquidity period for Gold.
OVERLAP_WINDOW = (time(7, 0), time(10, 0))

# Sessions where Gold strategies should actively trade.
ACTIVE_SESSIONS = ("london", "ny_am")

# Sessions where scalping (high-frequency) is allowed.
SCALP_SESSIONS = ("ny_am",)  # overlap = peak liquidity = tightest spreads


@dataclass(slots=True)
class SessionInfo:
    """Classification of a single timestamp into its trading session."""

    session: str              # e.g. "london", "ny_am", "asian"
    is_active: bool           # True if session is in ACTIVE_SESSIONS
    is_overlap: bool          # True if in London-NY overlap window
    is_scalp_allowed: bool    # True if high-frequency scalping is safe
    ny_time: datetime         # The timestamp converted to NY local time


@dataclass(slots=True)
class AsianRange:
    """The Asian session's high and low — the anchor for London strategies."""

    high: Decimal
    low: Decimal
    midpoint: Decimal
    range_width: Decimal      # high - low
    session_date: date        # The NY date of the Asian session
    bar_count: int            # Number of bars used to compute

    @property
    def is_valid(self) -> bool:
        """A range is valid if it has sufficient bars and non-zero width."""
        return self.bar_count >= 4 and self.range_width > Decimal("0")


def _to_ny(ts: datetime) -> datetime:
    """Convert any timestamp to NY local time."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(NY)


def classify_session(ts: datetime) -> SessionInfo:
    """Determine which trading session a timestamp belongs to.

    Returns a SessionInfo with the session name, activity flags, and
    the NY-local conversion of the input timestamp.
    """
    ny = _to_ny(ts)
    local_t = ny.time()

    session = "off_hours"
    for name, (start, end) in SESSION_WINDOWS.items():
        if name == "asian":
            # Asian wraps midnight: 20:00–23:59:59
            if local_t >= start:
                session = name
                break
        else:
            if start <= local_t < end:
                session = name
                break

    is_overlap = OVERLAP_WINDOW[0] <= local_t < OVERLAP_WINDOW[1]

    return SessionInfo(
        session=session,
        is_active=session in ACTIVE_SESSIONS,
        is_overlap=is_overlap,
        is_scalp_allowed=session in SCALP_SESSIONS,
        ny_time=ny,
    )


def is_in_session(ts: datetime, session_name: str) -> bool:
    """Check if a timestamp falls within a specific session."""
    info = classify_session(ts)
    return info.session == session_name


def compute_asian_range_from_bars(
    bars: Sequence[tuple[datetime, Decimal, Decimal]],
) -> AsianRange | None:
    """Compute the Asian session range from a sequence of (timestamp, high, low) tuples.

    Bars should be from the Asian session window (20:00–02:00 NY time).
    Returns None if insufficient bars.
    """
    if not bars:
        return None

    highs = [h for _, h, _ in bars]
    lows = [l for _, _, l in bars]

    session_high = max(highs)
    session_low = min(lows)
    midpoint = (session_high + session_low) / Decimal("2")
    range_width = session_high - session_low

    # Use the first bar's date as the session date
    first_ts = _to_ny(bars[0][0])
    session_date = first_ts.date()

    ar = AsianRange(
        high=session_high,
        low=session_low,
        midpoint=midpoint,
        range_width=range_width,
        session_date=session_date,
        bar_count=len(bars),
    )
    return ar if ar.is_valid else None


async def compute_asian_range(
    pool, symbol: str, reference_date: date, timeframe: str = "15min"
) -> AsianRange | None:
    """Compute the Asian session high/low from stored candles.

    ``reference_date`` is the NY-local date — the Asian session for that date
    starts at 20:00 the previous day (NY time) and ends at 02:00 of the
    reference date.

    Returns None if there aren't enough bars to form a reliable range.
    """
    # Asian session: 20:00 prev day → 02:00 reference day (all in NY time).
    prev_day = reference_date - timedelta(days=1)
    asian_start = datetime.combine(prev_day, time(20, 0), tzinfo=NY)
    asian_end = datetime.combine(reference_date, time(2, 0), tzinfo=NY)

    # Convert to UTC for the database query.
    start_utc = asian_start.astimezone(timezone.utc)
    end_utc = asian_end.astimezone(timezone.utc)

    rows = await pool.fetch(
        """
        SELECT "timestamp", "high", "low"
        FROM "Candle"
        WHERE "symbol" = $1
          AND "timeframe" = $2
          AND "timestamp" >= $3
          AND "timestamp" < $4
        ORDER BY "timestamp" ASC
        """,
        symbol,
        timeframe,
        start_utc,
        end_utc,
    )

    if not rows:
        return None

    bars = [
        (r["timestamp"], Decimal(str(r["high"])), Decimal(str(r["low"])))
        for r in rows
    ]

    return compute_asian_range_from_bars(bars)


def session_risk_label(ts: datetime) -> str:
    """Return a human-readable risk label for the current session.

    Used by the risk engine to apply session-specific risk budgets.
    """
    info = classify_session(ts)
    if info.session in ("asian", "asian_late"):
        return "ASIAN"
    elif info.session in ("london", "london_extended"):
        return "LONDON"
    elif info.session in ("ny_am", "ny_pm"):
        return "NEWYORK"
    return "OFF_HOURS"
