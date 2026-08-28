"""Data-freshness weekend handling and the backtest job's argument building.

The weekend rule is the subtle one: FX/metals venues close Fri 22:00 → Sun 22:00
UTC, so a healthy-but-closed series must not page anyone, while crypto trades
through and stays measured against the real clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.execution.backtest_runner import DEFAULTS, build_args
from app.domain.execution.data_freshness import (
    STALE_LIMIT,
    WEEKEND_OPEN_SYMBOLS,
    effective_now,
)

# 2026-08-28 is a Friday.
FRI_2100 = datetime(2026, 8, 28, 21, 0)
FRI_2300 = datetime(2026, 8, 28, 23, 0)
SAT_1200 = datetime(2026, 8, 29, 12, 0)
SUN_1200 = datetime(2026, 8, 30, 12, 0)
SUN_2300 = datetime(2026, 8, 30, 23, 0)
MON_1200 = datetime(2026, 8, 31, 12, 0)


class TestEffectiveNow:
    def test_inside_the_trading_week_the_clock_is_used_as_is(self):
        assert effective_now(MON_1200, "XAUUSD") == MON_1200
        assert effective_now(FRI_2100, "XAUUSD") == FRI_2100

    def test_friday_after_close_measures_from_the_friday_2200_close(self):
        assert effective_now(FRI_2300, "XAUUSD") == datetime(2026, 8, 28, 22, 0)

    def test_saturday_measures_from_the_friday_close(self):
        assert effective_now(SAT_1200, "XAUUSD") == datetime(2026, 8, 28, 22, 0)

    def test_sunday_before_reopen_measures_from_the_friday_close(self):
        assert effective_now(SUN_1200, "XAUUSD") == datetime(2026, 8, 28, 22, 0)

    def test_sunday_after_reopen_uses_the_real_clock(self):
        assert effective_now(SUN_2300, "XAUUSD") == SUN_2300

    def test_crypto_is_exempt_from_the_weekend_adjustment(self):
        assert "BTCUSD" in WEEKEND_OPEN_SYMBOLS
        assert effective_now(SAT_1200, "BTCUSD") == SAT_1200

    def test_a_weekend_gap_does_not_false_alarm_a_frozen_fx_series(self):
        # Newest 60min bar is the Friday 21:00 print; on Saturday noon the naive
        # age would be ~15h (stale), but measured from the close it is 1h (fresh).
        newest = datetime(2026, 8, 28, 21, 0)
        naive_age = SAT_1200 - newest
        adjusted_age = effective_now(SAT_1200, "XAUUSD") - newest
        assert naive_age > STALE_LIMIT["60min"]
        assert adjusted_age <= STALE_LIMIT["60min"]


class TestStaleLimits:
    def test_intraday_limits_are_two_bars_wide(self):
        assert STALE_LIMIT["1min"] == timedelta(minutes=2)
        assert STALE_LIMIT["5min"] == timedelta(minutes=10)
        assert STALE_LIMIT["15min"] == timedelta(minutes=30)
        assert STALE_LIMIT["60min"] == timedelta(hours=2)

    def test_the_daily_limit_spans_a_weekend(self):
        assert STALE_LIMIT["daily"] == timedelta(days=3)


class TestBuildArgs:
    def test_applies_the_defaults_for_an_empty_request(self):
        args = build_args({})
        assert args[0] == "backtester.py"
        assert "--save-db" in args
        assert str(DEFAULTS["balance"]) in args
        for tf in DEFAULTS["timeframes"]:
            assert tf in args

    def test_honours_explicit_selections(self):
        args = build_args(
            {"timeframes": ["15min"], "symbols": ["XAUUSD"], "strategies": ["trend_ema"]}
        )
        assert args[args.index("--timeframes") + 1] == "15min"
        assert args[args.index("--symbols") + 1] == "XAUUSD"
        assert args[args.index("--strategies") + 1] == "trend_ema"

    def test_passes_the_no_costs_switch_only_when_requested(self):
        assert "--no-costs" not in build_args({})
        assert "--no-costs" in build_args({"noCosts": True})

    def test_passes_a_label_through(self):
        args = build_args({"label": "phase-7 soak"})
        assert args[args.index("--label") + 1] == "phase-7 soak"
