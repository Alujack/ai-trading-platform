"""ICT killzones — time-of-day gating (concepts §3.8).

ICT setups cluster in a few New-York-time windows; restricting signals to them is
one of the few parts of the methodology with real empirical support (it mainly
curbs overtrading — concepts §4). Windows are defined in **America/New_York** and
resolved through ``zoneinfo`` so DST is handled correctly rather than with a fixed
UTC offset (concepts §3.8: "compute windows from a tz library").

Candle timestamps from TimescaleDB are tz-aware UTC; hand-built test fixtures are
naive and assumed UTC. Daily (and higher) bars have no meaningful intraday time,
so callers skip killzone gating for them via :func:`timeframe_is_intraday`.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# (start, end) in NY local time, end-exclusive. EST/EDT handled by zoneinfo.
KILLZONES: dict[str, tuple[time, time]] = {
    "london": (time(2, 0), time(5, 0)),       # often sweeps the Asian range
    "ny_am": (time(7, 0), time(10, 0)),       # continues or reverses London
    "silver_bullet": (time(10, 0), time(11, 0)),  # morning-FVG continuation window
}

# Default gate: the two primary directional windows. Silver Bullet is a subset of
# NY-AM hours and is carried separately for the dedicated detector (build plan §3).
DEFAULT_KILLZONES = ("london", "ny_am")

_INTRADAY_SUFFIXES = ("min", "h", "hour")


def timeframe_is_intraday(timeframe: str) -> bool:
    """True for sub-daily bars (15min, 60min, 1h, …); False for daily/weekly."""
    tf = timeframe.lower()
    return any(tf.endswith(s) for s in _INTRADAY_SUFFIXES) or tf.endswith("m")


def _to_ny(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(NY)


def active_killzone(ts: datetime, enabled: tuple[str, ...] | None = None) -> str | None:
    """Name of the killzone ``ts`` falls in (NY time), or None. ``enabled``
    restricts which windows count; defaults to all defined windows."""
    names = enabled if enabled is not None else tuple(KILLZONES.keys())
    local = _to_ny(ts).time()
    for name in names:
        start, end = KILLZONES[name]
        if start <= local < end:
            return name
    return None


def in_killzone(ts: datetime, enabled: tuple[str, ...] | None = None) -> bool:
    return active_killzone(ts, enabled) is not None
