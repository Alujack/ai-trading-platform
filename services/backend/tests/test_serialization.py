"""Wire-contract parity: the JSON shapes the dashboard was written against.

Three conventions must survive the language change (plan §7):
* `Decimal` columns serialize as strings in Decimal.js `toFixed()` form.
* Timestamps are `Date.prototype.toISOString()` — UTC, exactly 3 fractional
  digits, `Z` suffix.
* Field names stay camelCase.

The expected values below were checked against the Node/Prisma output, so a
regression here is a visible break in the dashboard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.serialization import (
    dec,
    decimal_str,
    iso,
    js_number,
    num,
    num_or_nan,
    num_or_none,
    ser,
)


class TestDecimalStr:
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            # DECIMAL(18,8) values come back from asyncpg with the full scale;
            # Decimal.js drops trailing fractional zeros, and so must we.
            ("2650.00000000", "2650"),
            ("1.50000000", "1.5"),
            ("0.00000000", "0"),
            ("0.00000001", "0.00000001"),
            ("-12.34000000", "-12.34"),
            ("100", "100"),
            ("1234567890.12345678", "1234567890.12345678"),
        ],
    )
    def test_matches_prisma_decimal_to_fixed(self, stored: str, expected: str):
        assert decimal_str(Decimal(stored)) == expected

    def test_never_uses_exponent_notation(self):
        # `Decimal.normalize()` would render this as 1E+10 — which would break
        # every client parsing the field as a numeric string.
        assert decimal_str(Decimal("10000000000.00000000")) == "10000000000"
        assert decimal_str(Decimal("0.00000010")) == "0.0000001"


class TestIso:
    def test_matches_javascript_to_iso_string(self):
        assert iso(datetime(2026, 8, 28, 10, 30, 15, 123000, tzinfo=timezone.utc)) == (
            "2026-08-28T10:30:15.123Z"
        )

    def test_always_emits_exactly_three_fractional_digits(self):
        assert iso(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-01-01T00:00:00.000Z"

    def test_truncates_sub_millisecond_precision(self):
        assert iso(datetime(2026, 1, 1, 0, 0, 0, 999999, tzinfo=timezone.utc)) == (
            "2026-01-01T00:00:00.999Z"
        )

    def test_treats_a_naive_value_as_utc(self):
        # The `TIMESTAMP(3)` columns are naive; reading them as local time would
        # shift every timestamp the dashboard renders.
        assert iso(datetime(2026, 8, 28, 10, 0, 0)) == "2026-08-28T10:00:00.000Z"

    def test_converts_an_offset_aware_value_to_utc(self):
        aware = datetime(2026, 8, 28, 12, 0, tzinfo=timezone(timedelta(hours=2)))
        assert iso(aware) == "2026-08-28T10:00:00.000Z"


class TestSer:
    def test_walks_nested_containers(self):
        payload = {
            "entryPrice": Decimal("2650.00000000"),
            "createdAt": datetime(2026, 8, 28, tzinfo=timezone.utc),
            "trades": [{"profitLoss": Decimal("-10.50"), "exitPrice": None}],
            "pagination": {"limit": 50, "offset": 0, "total": 3},
        }
        assert ser(payload) == {
            "entryPrice": "2650",
            "createdAt": "2026-08-28T00:00:00.000Z",
            "trades": [{"profitLoss": "-10.5", "exitPrice": None}],
            "pagination": {"limit": 50, "offset": 0, "total": 3},
        }

    def test_leaves_primitives_untouched(self):
        assert ser({"a": 1, "b": True, "c": "x", "d": None, "e": 1.5}) == {
            "a": 1,
            "b": True,
            "c": "x",
            "d": None,
            "e": 1.5,
        }

    def test_renders_enums_by_value(self):
        from app.db.enums import Direction, SignalStatus

        assert ser({"direction": Direction.LONG, "status": SignalStatus.PENDING}) == {
            "direction": "LONG",
            "status": "PENDING",
        }


class TestNumericHelpers:
    def test_num_treats_null_as_zero(self):
        assert num(None) == 0.0
        assert num(Decimal("2.5")) == 2.5

    def test_num_or_nan_propagates_null_as_nan(self):
        assert num_or_nan(None) != num_or_nan(None)  # NaN != NaN
        assert num_or_nan(Decimal("2.5")) == 2.5

    def test_num_or_none_preserves_null(self):
        assert num_or_none(None) is None
        assert num_or_none(Decimal("2.5")) == 2.5

    def test_dec_quantizes_like_to_fixed(self):
        assert str(dec(2650.123456789, 8)) == "2650.12345679"
        assert str(dec(-10.505, 2)) in ("-10.50", "-10.51")  # banker's-rounding tolerant
        assert str(dec(1, 2)) == "1.00"


class TestJsNumber:
    """`JSON.stringify` renders an integral JS number without a decimal point.

    Python would emit `100.0` where Express emitted `100`; every computed
    numeric response field goes through `js_number` so the bodies match.
    """

    def test_narrows_an_integral_float_to_int(self):
        assert js_number(100.0) == 100
        assert isinstance(js_number(100.0), int)

    def test_keeps_a_fractional_float(self):
        assert js_number(1.5) == 1.5
        assert isinstance(js_number(1.5), float)

    def test_passes_ints_through(self):
        assert js_number(5) == 5
        assert isinstance(js_number(5), int)

    def test_preserves_null(self):
        assert js_number(None) is None

    def test_passes_non_finite_values_through(self):
        # The encoder maps these to null — exactly what JSON.stringify(Infinity) does.
        import math

        assert js_number(math.inf) == math.inf
        assert math.isnan(js_number(math.nan))  # type: ignore[arg-type]

    def test_negative_integral_values_narrow_too(self):
        assert js_number(-42.0) == -42
        assert isinstance(js_number(-42.0), int)
