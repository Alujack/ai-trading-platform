"""Wire-contract serialization — byte-for-byte parity with the Express API.

The dashboard was written against Prisma's JSON output, so three conventions
have to survive the language change (plan §7):

* `Decimal` columns are emitted as **strings** in Decimal.js `toFixed()` form,
  which drops trailing fractional zeros: DECIMAL(18,8) `2650.00000000` → `"2650"`.
* Timestamps are `Date.prototype.toISOString()`: UTC, always exactly three
  fractional digits, `Z` suffix.
* Field names stay camelCase (they already are — the columns are camelCase).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


def decimal_str(value: Decimal | int | float | str) -> str:
    """Render a decimal the way `Prisma.Decimal.toFixed()` does.

    Fixed-point (never exponent) notation with trailing fractional zeros
    removed, so a value stored as `1.50000000` serializes as `"1.5"`.
    """
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-"):
        text = "0"
    return text


def iso(value: datetime) -> str:
    """`Date.prototype.toISOString()`: UTC, millisecond precision, `Z` suffix.

    Naive values are treated as UTC — the Prisma columns are `TIMESTAMP(3)`
    without a time zone and the whole platform stores UTC.
    """
    dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{dt.microsecond // 1000:03d}Z"


def ser(value: Any) -> Any:
    """Recursive serializer mirroring `apps/api/src/lib/decimal.ts`.

    Decimals become strings, datetimes become ISO strings, and containers are
    walked; everything else passes through untouched.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return decimal_str(value)
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: ser(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [ser(v) for v in value]
    return value


def js_number(value: float | int | None) -> float | int | None:
    """Render a number the way `JSON.stringify` renders a JavaScript number.

    JS has one numeric type, so an integral value serializes without a decimal
    point: `Math.round(100)` becomes `100`, never `100.0`. Python would emit
    `100.0` and drift from the bodies the dashboard was built against, so
    integral floats are narrowed to `int` here.

    Non-finite values are passed through untouched; the JSON encoder maps them to
    `null`, which is exactly what `JSON.stringify(Infinity)` does too.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, float) or not math.isfinite(value):
        return value
    return int(value) if value.is_integer() else value


def num(value: Decimal | int | float | str | None) -> float:
    """Decimal → float, mirroring the TypeScript `Number(d.toString())` helper.

    Returns `0.0` for null, matching the `num()` used by the positions/journal
    routes (the risk paths use :func:`num_or_nan` instead).
    """
    if value is None:
        return 0.0
    return float(value)


def num_or_nan(value: Decimal | int | float | str | None) -> float:
    """Decimal → float with `NaN` for null — the execution-path variant."""
    if value is None:
        return float("nan")
    return float(value)


def num_or_none(value: Decimal | int | float | str | None) -> float | None:
    """Decimal → float preserving null."""
    return None if value is None else float(value)


def dec(value: float | int | Decimal, places: int) -> Decimal:
    """Quantize a number for a DECIMAL column, mirroring `.toFixed(places)`.

    The Express writers passed `Number.toFixed(8)` / `toFixed(2)` strings to
    Prisma; going through `Decimal(str(...))` keeps the same rounding.
    """
    return Decimal(f"{float(value):.{places}f}")
