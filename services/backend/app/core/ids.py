"""Primary-key generation for the Prisma-created tables.

Prisma applies `@default(cuid())` client-side, so the columns have no database
default and every writer must supply an id. The Python ingestion worker already
writes `uuid4().hex` into these tables (`services/data/db.py`), and the backtest
job parser matches `id=([a-f0-9]{24,32})`, so the backend uses the same shape.
"""
from __future__ import annotations

import uuid


def new_id() -> str:
    """A 32-char lowercase-hex id, interchangeable with the existing rows."""
    return uuid.uuid4().hex
