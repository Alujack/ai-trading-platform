"""Broker (MT5) credential management — port of `routes/brokers.routes.ts`.

Lets the user set their MT5 account login/password/server from Settings → Broker
instead of `.env`. The password is encrypted at rest and never returned. A "test"
pushes the creds to the bridge (`POST /session/login`) and reports pass/fail.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from ...core.security import KEY_HELP, is_encryption_configured
from ...domain.execution.broker import ensure_broker_session
from ...domain.execution.broker.credentials import (
    BrokerCredentialInput,
    get_active_credential,
    get_credential_status,
    record_test_result,
    save_credential,
)
from ..dependencies import Db

router = APIRouter(tags=["broker"])


class SaveCredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: int = Field(gt=0)
    password: str = Field(min_length=1, max_length=256)
    server: str = Field(min_length=1, max_length=120)
    env: Literal["demo", "real"] = "demo"


@router.get("/api/brokers/credentials")
async def get_credentials(session: Db) -> dict[str, Any]:
    """Current credential status — secret-free. Reports whether ENCRYPTION_KEY is set."""
    status = await get_credential_status(session)
    return {**status, "encryptionReady": is_encryption_configured()}


@router.put("/api/brokers/credentials")
async def put_credentials(
    body: SaveCredentialBody, session: Db, response: Response
) -> dict[str, Any]:
    """Save (replace) the active MT5 credential. Encrypts the password."""
    if not is_encryption_configured():
        response.status_code = 400
        return {
            "error": (
                "ENCRYPTION_KEY is not set on the server — cannot store secrets. "
                + KEY_HELP
            )
        }
    await save_credential(
        session,
        BrokerCredentialInput(
            login=body.login, password=body.password, server=body.server, env=body.env
        ),
    )
    return {"ok": True, **await get_credential_status(session)}


@router.post("/api/brokers/credentials/test")
async def test_credentials(session: Db, response: Response) -> dict[str, Any]:
    """Test the active credential by logging the bridge terminal into the account."""
    cred = await get_active_credential(session)
    if cred is None:
        response.status_code = 400
        return {"error": "no broker credentials saved yet"}
    result = await ensure_broker_session(session)
    await record_test_result(session, cred.id, result)
    return result
