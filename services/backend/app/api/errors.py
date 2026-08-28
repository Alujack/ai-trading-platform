"""Error shape parity with the Express `errorHandler` middleware.

The dashboard reads `{"error": "..."}` off every failure, and validation errors
carried a Zod `flatten()` payload under `details`. FastAPI's default
`{"detail": ...}` would break both, so the handlers below re-shape everything.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("backend.api")


class HttpError(Exception):
    """Ported from `errors/httpError.ts` — a status + message + optional details."""

    def __init__(self, status: int, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details


class NotFoundError(HttpError):
    def __init__(self, message: str = "Not found") -> None:
        super().__init__(404, message)


def _zod_flatten(exc: RequestValidationError) -> dict[str, Any]:
    """Render Pydantic errors in Zod's `flatten()` shape.

    `{ formErrors: [...], fieldErrors: { field: [messages] } }` — the same
    structure the TypeScript `validate()` middleware attached, so any client
    already parsing it keeps working.
    """
    form_errors: list[str] = []
    field_errors: dict[str, list[str]] = {}
    for err in exc.errors():
        # loc is e.g. ("query", "limit") or ("body", "entryPrice"); drop the source.
        path = [str(p) for p in err.get("loc", ()) if p not in ("query", "body", "path", "header")]
        message = str(err.get("msg", "Invalid input"))
        if not path:
            form_errors.append(message)
            continue
        field_errors.setdefault(path[0], []).append(message)
    return {"formErrors": form_errors, "fieldErrors": field_errors}


def install_error_handlers(app: FastAPI) -> None:
    """Register the Express-compatible handlers on the app."""

    @app.exception_handler(HttpError)
    async def _http_error(_request: Request, exc: HttpError) -> JSONResponse:
        body: dict[str, Any] = {"error": exc.message}
        if exc.details is not None:
            body["details"] = exc.details
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "Validation failed", "details": _zod_flatten(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _starlette(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404 and exc.detail == "Not Found":
            # Mirror `notFoundHandler`'s message so unknown routes read the same.
            return JSONResponse(
                status_code=404,
                content={"error": f"Route not found: {request.method} {request.url.path}"},
            )
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("[error] %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"error": "Internal server error"})
