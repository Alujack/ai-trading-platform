"""Broker (MT5) credential store — port of `execution/broker/credentials.ts`.

Persists UI-entered MT5 account creds with the password encrypted at rest, and
hands the decrypted creds to the broker layer only when pushing a session to the
bridge. Single-account v1: exactly one row is `isActive`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.ids import new_id
from ....core.security import decrypt, encrypt
from ....core.serialization import iso
from ....db.models import BrokerCredential

BrokerEnv = Literal["demo", "real"]


@dataclass(slots=True)
class BrokerCredentialInput:
    login: int
    password: str
    server: str
    env: BrokerEnv = "demo"


@dataclass(slots=True)
class ActiveCredential:
    """Decrypted credential — keep in memory only, never log or return to a client."""

    id: str
    login: int
    password: str
    server: str
    env: BrokerEnv


async def save_credential(session: AsyncSession, data: BrokerCredentialInput) -> None:
    """Save (replace) the active credential; encrypts the password, deactivates prior rows."""
    password_enc = encrypt(data.password)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.execute(
        update(BrokerCredential)
        .where(BrokerCredential.isActive.is_(True))
        .values(isActive=False, updatedAt=now)
    )
    session.add(
        BrokerCredential(
            id=new_id(),
            broker="exness",
            login=data.login,
            passwordEnc=password_enc,
            server=data.server,
            env=data.env,
            isActive=True,
            createdAt=now,
            updatedAt=now,
        )
    )
    await session.commit()


async def _active_row(session: AsyncSession) -> BrokerCredential | None:
    return (
        await session.execute(
            select(BrokerCredential)
            .where(BrokerCredential.isActive.is_(True))
            .order_by(BrokerCredential.updatedAt.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_active_credential(session: AsyncSession) -> ActiveCredential | None:
    """The active credential with its password decrypted, or None if unconfigured."""
    row = await _active_row(session)
    if row is None:
        return None
    return ActiveCredential(
        id=row.id,
        login=row.login,
        password=decrypt(row.passwordEnc),
        server=row.server,
        env="real" if row.env == "real" else "demo",
    )


async def get_credential_status(session: AsyncSession) -> dict[str, Any]:
    """Secret-free status for the settings UI."""
    row = await _active_row(session)
    if row is None:
        return {
            "configured": False,
            "login": None,
            "server": None,
            "env": None,
            "hasPassword": False,
            "lastTest": None,
            "updatedAt": None,
        }
    return {
        "configured": True,
        "login": row.login,
        "server": row.server,
        "env": "real" if row.env == "real" else "demo",
        "hasPassword": bool(row.passwordEnc),
        "lastTest": row.lastTest,
        "updatedAt": iso(row.updatedAt),
    }


async def record_test_result(
    session: AsyncSession, credential_id: str, result: dict[str, Any]
) -> None:
    """Record the outcome of a `/session/login` test against a credential row."""
    row = (
        await session.execute(select(BrokerCredential).where(BrokerCredential.id == credential_id))
    ).scalar_one_or_none()
    if row is None:
        return
    row.lastTest = {
        "ok": bool(result.get("ok")),
        "detail": result.get("detail"),
        "testedAt": iso(datetime.now(timezone.utc)),
    }
    row.updatedAt = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.commit()
