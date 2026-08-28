"""Postgres enum types created by the Prisma migrations.

Values and type names must match the existing database exactly — SQLAlchemy is
adopting the schema, not recreating it. Every enum is declared with
`create_type=False` at the column so Alembic never tries to re-CREATE TYPE.
"""
from __future__ import annotations

from enum import StrEnum


class Impact(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class TradeStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class ExecutionMode(StrEnum):
    OFF = "OFF"
    AUTO = "AUTO"
    CONFIRM = "CONFIRM"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class RawVerdict(StrEnum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"
