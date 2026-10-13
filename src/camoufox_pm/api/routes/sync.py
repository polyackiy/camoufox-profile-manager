"""Owner enrollment requires a live administrator cookie, never API-key authority.

Crypto is deliberately absent from module imports: a manager without the optional
sync extra can register this router and report the service unavailable safely.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from camoufox_pm.api.access import request_principal, request_session_hash
from camoufox_pm.api.dependencies import get_storage_manager
from camoufox_pm.api.principal import Principal
from camoufox_pm.core import auth
from camoufox_pm.core.profile_operations import run_file_operation

router = APIRouter()
_service: Any = None
_directory: Path | None = None
_operation_in_progress = False
_MAX_BODY = 128 * 1024
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def set_sync_service(service: Any | None) -> None:
    """Register the initialized installation service; no implicit enrollment."""
    global _service
    _service = service


def set_sync_directory(directory: Path | None) -> None:
    """Configure lazy optional service construction, without opening keys at startup."""
    global _directory, _service
    _directory = directory
    _service = None


def _resolve_service():
    global _service
    if _service is None:
        if _directory is None:
            raise _error(503, "Synchronization service is unavailable")
        try:
            from camoufox_pm.sync.service import SyncService
        except ImportError:
            raise _error(503, "Install the synchronization extra to enable this service") from None
        _service = SyncService(_directory)
    return _service


def _error(status: int, message: str) -> HTTPException:
    return HTTPException(status, message, headers=_NO_STORE)


def _response(value: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers=_NO_STORE)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EnrollmentBody(_Body):
    relay_url: str = Field(min_length=1, max_length=2048)
    device_label: str = Field(min_length=1, max_length=80)
    vault_kind: Literal["native", "encrypted_file"]
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)


class PasswordBody(_Body):
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)


class RecoveryBody(PasswordBody):
    package: SecretStr = Field(min_length=1, max_length=120000)
    secret: SecretStr = Field(min_length=43, max_length=43)


def _origin(url: str) -> tuple[str, str | None, int | None]:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        return (
            parsed.scheme,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except ValueError:
        raise _error(403, "Same-origin request required") from None


def _json_mutation(request: Request) -> None:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise _error(415, "JSON content type required")
    origins = request.headers.getlist("origin")
    if (
        len(origins) > 1
        or (origins and _origin(origins[0]) != _origin(str(request.base_url)))
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise _error(403, "Same-origin request required")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


async def _body(request: Request, model: type[_Body]) -> Any:
    _json_mutation(request)
    size = request.headers.get("content-length")
    if size:
        try:
            if int(size) < 0 or int(size) > _MAX_BODY:
                raise ValueError
        except ValueError:
            raise _error(413, "Enrollment request is too large") from None
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_BODY:
            raise _error(413, "Enrollment request is too large")
        raw.extend(chunk)
    try:
        # Every accepted body is a flat object of scalar values. Bound parser
        # recursion without having to echo Pydantic's invalid secret input.
        decoded = json.loads(raw, object_pairs_hook=_unique_pairs)
        return model.model_validate(decoded)
    except (ValueError, TypeError, RecursionError):
        raise _error(422, "Invalid sync enrollment request") from None


async def _live_owner(request: Request, expected_id: str | None = None) -> str:
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token or len(token) > 512:
        raise _error(401, "Log in with an administrator account to manage synchronization")
    storage = get_storage_manager()
    username = await auth.validate_session(storage, token, read_only=True)
    user = await storage.get_user_by_username(username) if username is not None else None
    session = await storage.get_session(auth.hash_token(token))
    if user is None or session is None or session["user_id"] != user["id"]:
        raise _error(401, "Administrator session is no longer valid")
    if user["role"] != "administrator" or (expected_id is not None and user["id"] != expected_id):
        raise _error(403, "A live administrator owner session is required")
    principal = Principal("session", user["username"], user["role"])
    request.state.principal = principal
    request_principal.set(principal)
    request_session_hash.set(auth.hash_token(token))
    return user["id"]


async def _call(request: Request, user_id: str, method: str, *args, **kwargs):
    global _operation_in_progress
    if _service is None and _directory is None:
        raise _error(503, "Synchronization service is unavailable")
    if _operation_in_progress:
        raise _error(409, "Another synchronization ceremony is in progress")
    _operation_in_progress = True
    loop = asyncio.get_running_loop()

    def authorize() -> None:
        asyncio.run_coroutine_threadsafe(_live_owner(request, user_id), loop).result()

    def invoke():
        service = _resolve_service()
        return getattr(service, method)(user_id, *args, authorize=authorize, **kwargs)

    try:
        # The synchronous service performs KDFs and isolated recovery verification.
        # Shield+drain means disconnect/cancellation cannot release this reservation
        # while a worker is still mutating protected enrollment state.
        return await run_file_operation(invoke)
    except HTTPException:
        raise
    except Exception:
        # Includes optional-library, key-store, malformed-package and service
        # errors; never serialize exception text containing credentials.
        raise _error(
            409, "Synchronization ceremony failed; check setup and unlock credentials"
        ) from None
    finally:
        _operation_in_progress = False


def _password(body: PasswordBody | EnrollmentBody) -> str | None:
    return body.password.get_secret_value() if body.password is not None else None


@router.get("/sync/status", operation_id="sync_enrollment_status")
async def status(request: Request):
    user_id = await _live_owner(request)
    return _response(await _call(request, user_id, "status"))


@router.post("/sync/enrollment", operation_id="begin_sync_enrollment")
async def enrollment(request: Request):
    user_id = await _live_owner(request)
    body: EnrollmentBody = await _body(request, EnrollmentBody)
    result = await _call(
        request,
        user_id,
        "begin_enrollment",
        body.relay_url,
        body.device_label,
        body.vault_kind,
        _password(body),
    )
    return _response(result, 201)


@router.post("/sync/recovery-package", operation_id="export_sync_recovery_package")
async def recovery_package(request: Request):
    user_id = await _live_owner(request)
    body: PasswordBody = await _body(request, PasswordBody)
    package, secret = await _call(request, user_id, "recovery_package", _password(body))
    return _response(
        {"package": base64.urlsafe_b64encode(package).decode("ascii").rstrip("="), "secret": secret}
    )


@router.post("/sync/confirm-recovery", operation_id="confirm_sync_recovery")
async def confirm_recovery(request: Request):
    user_id = await _live_owner(request)
    body: RecoveryBody = await _body(request, RecoveryBody)
    encoded = body.package.get_secret_value()
    try:
        package = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        if base64.urlsafe_b64encode(package).decode("ascii").rstrip("=") != encoded:
            raise ValueError
    except ValueError:
        raise _error(422, "Invalid recovery package encoding") from None
    result = await _call(
        request,
        user_id,
        "confirm_recovery",
        package,
        body.secret.get_secret_value(),
        _password(body),
    )
    return _response(result)


@router.post("/sync/abandon", operation_id="abandon_sync_enrollment")
async def abandon(request: Request):
    user_id = await _live_owner(request)
    await _body(request, _Body)
    await _call(request, user_id, "abandon_enrollment")
    return _response({"success": True})
