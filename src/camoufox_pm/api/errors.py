"""One error shape for the whole API.

Every non-2xx response carries the same body:

    {"error": {"code": "...", "message": "...", "details": [...] | null},
     "detail": "..."}

``error`` is the contract; ``detail`` mirrors ``error.message`` for clients
written against FastAPI's default shape and goes away in 1.0. Handlers here
catch both plain ``HTTPException`` (routes, the auth guard, 405s from Starlette)
and request validation, so a validation failure is no longer the one error that
looked different.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Codes are keyed by status so a plain HTTPException still yields a stable,
# machine-readable code without every raise site naming one.
DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthorized",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    500: "internal_error",
    503: "unavailable",
}


class ApiError(HTTPException):
    """An ``HTTPException`` that names its error code and structured details."""

    def __init__(
        self,
        status_code: int,
        message: str,
        code: str | None = None,
        details: Sequence[Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code or DEFAULT_CODES.get(status_code, "error")
        self.details = details


def _payload(code: str, message: str, details: Sequence[Any] | None = None) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": jsonable_encoder(details)},
        "detail": message,
    }


def install_error_handlers(app: FastAPI) -> None:
    """Route every error FastAPI can produce through the one shape."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or DEFAULT_CODES.get(exc.status_code, "error")
        details = getattr(exc, "details", None)
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(code, message, details),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload("validation_error", validation_message(exc.errors()), exc.errors()),
        )


def validation_message(errors: Sequence[Mapping[str, Any]]) -> str:
    """Flatten pydantic's error list into one readable sentence.

    The structured list still travels in ``error.details``; this is so ``detail``
    and ``error.message`` stay strings even for validation failures.
    """
    parts = []
    for error in errors[:3]:
        location = ".".join(str(piece) for piece in error.get("loc", ()))
        message = error.get("msg", "invalid value")
        parts.append(f"{location}: {message}" if location else message)
    if len(errors) > 3:
        parts.append(f"and {len(errors) - 3} more")
    return "Validation failed — " + "; ".join(parts)
