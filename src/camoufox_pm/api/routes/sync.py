"""Owner enrollment requires a live administrator cookie, never API-key authority.

Crypto is deliberately absent from module imports: a manager without the optional
sync extra can register this router and report the service unavailable safely.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from camoufox_pm.api.access import request_principal, request_session_hash
from camoufox_pm.api.dependencies import get_storage_manager
from camoufox_pm.api.principal import Principal
from camoufox_pm.core import auth
from camoufox_pm.core.profile_operations import run_file_operation

router = APIRouter()
_service: Any = None
_directory: Path | None = None
_operation_in_progress = False
_checkpoint_provider: Callable[[], tuple] | None = None
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


def set_sync_checkpoint_provider(provider: Callable[[], tuple] | None) -> None:
    """Install a trusted protected-state reader; never supplied by an HTTP request."""
    global _checkpoint_provider
    _checkpoint_provider = provider


def _ceremonies(service):
    from camoufox_pm.sync.ceremonies import DeviceCeremonies, protected_checkpoints

    def checkpoints():
        if _checkpoint_provider is not None:
            return _checkpoint_provider()
        return protected_checkpoints(service)

    return DeviceCeremonies(service, checkpoints=checkpoints)


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


class TakeoverBody(PasswordBody):
    acknowledge_lost_device: Literal[True]

    @field_validator("acknowledge_lost_device", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class DeletionBody(PasswordBody):
    acknowledge_deletion: Literal[True]

    @field_validator("acknowledge_deletion", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class RestoreBody(PasswordBody):
    acknowledge_restore: Literal[True]

    @field_validator("acknowledge_restore", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class PrepareBody(PasswordBody):
    root: dict[str, Any]
    device_label: str = Field(min_length=1, max_length=80)
    vault_kind: Literal["native", "encrypted_file"]


class ApproveBody(PasswordBody):
    request: dict[str, Any]
    confirmed_fingerprint: str = Field(min_length=64, max_length=64)


class AcceptBody(PasswordBody):
    approval: dict[str, Any]
    confirmed_fingerprint: str = Field(min_length=64, max_length=64)


class ConnectBody(PasswordBody):
    registration_token: SecretStr = Field(min_length=1, max_length=4096)


class RevokeBody(PasswordBody):
    device_id: str = Field(min_length=36, max_length=36)


class ShareProfilesBody(PasswordBody):
    target_device_id: str = Field(min_length=36, max_length=36)


class AcceptProfilesBody(_Body):
    pack: dict[str, Any]


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
        # Bound nested public ceremony objects before parsing; quoted braces do
        # not count. Never echo validation input containing unlock credentials.
        depth, quoted, escaped = 0, False, False
        for char in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif char == 92:
                    escaped = True
                elif char == 34:
                    quoted = False
            elif char == 34:
                quoted = True
            elif char in (91, 123):
                depth += 1
                if depth > 16:
                    raise ValueError
            elif char in (93, 125):
                depth -= 1

        def reject_constant(value):
            raise ValueError("Invalid JSON constant")

        decoded = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=reject_constant)
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


async def _call(
    request: Request, user_id: str, method: str, *args, ceremony=False, profile_code=False, **kwargs
):
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
        target = _ceremonies(service) if ceremony else service
        authorize()
        if profile_code:
            from camoufox_pm.sync.profile_invitation import accept_profile_code, create_profile_code

            operation = {
                "create_profile_code": create_profile_code,
                "accept_profile_code": accept_profile_code,
            }[method]
            result = operation(service, user_id, *args, authorize=authorize, **kwargs)
        elif ceremony and method in {"draft_status", "resume_pairing", "membership_status"}:
            result = _ceremony_state(target, user_id, method, args, authorize)
        else:
            if ceremony and method in {"prepare_pairing", "approve_request", "accept_approval"}:
                from camoufox_pm.sync.identity import OwnerRoot, PairingApproval, PairingRequest

                model = {
                    "prepare_pairing": OwnerRoot,
                    "approve_request": PairingRequest,
                    "accept_approval": PairingApproval,
                }[method]
                arguments = (model.model_validate(args[0]), *args[1:])
            else:
                arguments = args
            result = getattr(target, method)(user_id, *arguments, authorize=authorize, **kwargs)
        authorize()
        return result.model_dump(mode="json") if isinstance(result, BaseModel) else result

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


def _ceremony_state(target, user_id, method, args, authorize):
    """Expose only allowlisted public draft state after checking its local owner."""
    if method == "membership_status":
        target.service._read(user_id)
        record = target.control.get("ceremony/membership")
        if record is None:
            return None
        payload = record.payload
        if payload.get("local_user_id") != user_id:
            raise ValueError("Wrong ceremony owner")
        if payload.get("kind") not in {"pairing", "revocation"} or payload.get("phase") not in {
            "pending",
            "published",
        }:
            raise ValueError("Invalid membership ceremony state")
        return {"kind": payload["kind"], "phase": payload["phase"]}
    if target.control.get("ceremony/pairing") is None:
        if method == "draft_status":
            return None
        raise ValueError("No pairing draft")
    _, draft = target._draft(user_id)
    if method == "resume_pairing":
        if draft.phase not in {"preparing", "pending"}:
            raise ValueError("Pairing draft cannot be resumed")
        return target.prepare_pairing(
            user_id, draft.root, draft.device_label, draft.vault_kind, *args, authorize=authorize
        )
    from camoufox_pm.sync.identity import pairing_fingerprint

    return {
        "root": draft.root.model_dump(mode="json"),
        "request": draft.request.model_dump(mode="json"),
        "fingerprint": pairing_fingerprint(draft.request),
        "phase": draft.phase,
        "device_label": draft.device_label,
        "vault_kind": draft.vault_kind,
    }


@router.get("/sync/pairing/owner-code")
async def owner_code(request: Request):
    user_id = await _live_owner(request)
    return _response(await _call(request, user_id, "export_owner_code", ceremony=True))


@router.get("/sync/pairing/draft")
async def pairing_draft(request: Request):
    user_id = await _live_owner(request)
    return _response(await _call(request, user_id, "draft_status", ceremony=True))


@router.get("/sync/membership/pending")
async def membership_pending(request: Request):
    user_id = await _live_owner(request)
    return _response(await _call(request, user_id, "membership_status", ceremony=True))


@router.post("/sync/pairing/prepare")
async def pairing_prepare(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, PrepareBody)
    return _response(
        await _call(
            request,
            user_id,
            "prepare_pairing",
            body.root,
            body.device_label,
            body.vault_kind,
            _password(body),
            ceremony=True,
        )
    )


@router.post("/sync/pairing/approve")
async def pairing_approve(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, ApproveBody)
    return _response(
        await _call(
            request,
            user_id,
            "approve_request",
            body.request,
            body.confirmed_fingerprint,
            _password(body),
            ceremony=True,
        )
    )


@router.post("/sync/pairing/accept")
async def pairing_accept(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, AcceptBody)
    return _response(
        await _call(
            request,
            user_id,
            "accept_approval",
            body.approval,
            body.confirmed_fingerprint,
            _password(body),
            ceremony=True,
        )
    )


@router.post("/sync/pairing/resume")
async def pairing_resume(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, PasswordBody)
    return _response(
        await _call(request, user_id, "resume_pairing", _password(body), ceremony=True)
    )


@router.post("/sync/pairing/abandon")
async def pairing_abandon(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, PasswordBody)
    await _call(request, user_id, "abandon_pairing", _password(body), ceremony=True)
    return _response({"success": True})


@router.post("/sync/membership/resume")
async def membership_resume(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, PasswordBody)
    return _response(
        await _call(request, user_id, "resume_membership", _password(body), ceremony=True)
    )


@router.get("/sync/devices")
async def devices(request: Request):
    user_id = await _live_owner(request)
    return _response(await _call(request, user_id, "list_devices", ceremony=True))


@router.post("/sync/devices/revoke")
async def revoke_device(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, RevokeBody)
    return _response(
        await _call(
            request, user_id, "revoke_device", body.device_id, _password(body), ceremony=True
        )
    )


@router.post("/sync/relay/connect")
async def connect_relay(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, ConnectBody)
    return _response(
        await _call(
            request,
            user_id,
            "connect_relay",
            body.registration_token.get_secret_value(),
            _password(body),
            ceremony=True,
        )
    )


_runtime_factory: Callable[[], Any] | None = None


def set_sync_runtime_factory(factory: Callable[[], Any] | None) -> None:
    """Wire the manager's existing maintenance gate without importing main."""
    global _runtime_factory
    _runtime_factory = factory


class PreserveCopyBody(_Body):
    acknowledge_fork: Literal[True]
    name: str | None = Field(default=None, min_length=1, max_length=200)


class ImportProfileBody(PasswordBody):
    profile_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )


async def _runtime_call(request: Request, user_id: str, method: str, *args, **kwargs):
    global _operation_in_progress
    if _runtime_factory is None:
        raise _error(503, "Synchronization runtime is unavailable")
    if _operation_in_progress:
        raise _error(409, "Another synchronization ceremony is in progress")
    _operation_in_progress = True

    async def authorize():
        await _live_owner(request, user_id)

    async def invoke():
        await authorize()
        # Construction may open protected SQLite state, so keep it off the loop.
        runtime = await run_file_operation(_runtime_factory)
        runtime.service = await run_file_operation(_resolve_service)
        runtime.cache = runtime.service.control.directory / "transfers"
        result = await getattr(runtime, method)(user_id, *args, authorize=authorize, **kwargs)
        await authorize()
        return result

    task = asyncio.create_task(invoke())
    try:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep admission reserved until network, key and disk workers drain.
            # Repeated disconnect cancellation cannot release it prematurely.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
            task.result()
            raise
        return result
    except HTTPException:
        raise
    except Exception:
        raise _error(409, "Synchronization operation failed; inspect its state and retry") from None
    finally:
        _operation_in_progress = False


@router.get("/sync/profiles")
async def sync_profiles(request: Request):
    user_id = await _live_owner(request)
    return _response(await _runtime_call(request, user_id, "list_profiles"))


@router.post("/sync/profiles/share")
async def sync_share_profiles(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, ShareProfilesBody)
    return _response(
        await _call(
            request,
            user_id,
            "create_profile_code",
            body.target_device_id,
            password=_password(body),
            profile_code=True,
        )
    )


@router.post("/sync/profiles/accept-code")
async def sync_accept_profiles(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, AcceptProfilesBody)
    return _response(
        await _call(request, user_id, "accept_profile_code", body.pack, profile_code=True)
    )


@router.post("/sync/profiles/import")
async def sync_import_profile(request: Request):
    user_id = await _live_owner(request)
    body = await _body(request, ImportProfileBody)
    return _response(
        await _runtime_call(
            request, user_id, "bind_paired", body.profile_id, password=_password(body)
        )
    )


@router.post("/sync/profiles/{local_id}/{action}")
async def sync_profile_action(request: Request, local_id: str, action: str):
    user_id = await _live_owner(request)
    if len(local_id) != 8 or not local_id.isascii() or not local_id.isalnum():
        raise _error(422, "Invalid local profile identifier")
    if action in {"trash", "apply-trash", "restore"}:
        model = RestoreBody if action == "restore" else DeletionBody
        lifecycle_body = await _body(request, model)
        acknowledgement = {
            "acknowledge_restore" if action == "restore" else "acknowledge_deletion": True
        }
        return _response(
            await _runtime_call(
                request,
                user_id,
                action.replace("-", "_"),
                local_id,
                password=_password(lifecycle_body),
                **acknowledgement,
            )
        )
    if action == "takeover":
        takeover_body = await _body(request, TakeoverBody)
        return _response(
            await _runtime_call(
                request,
                user_id,
                "takeover",
                local_id,
                acknowledge_lost_device=takeover_body.acknowledge_lost_device,
                password=_password(takeover_body),
            )
        )
    if action == "preserve-copy":
        copy_body = await _body(request, PreserveCopyBody)
        return _response(
            await _runtime_call(
                request,
                user_id,
                "preserve_local_copy",
                local_id,
                acknowledge_fork=copy_body.acknowledge_fork,
                name=copy_body.name,
            )
        )
    if action not in {"enable", "handoff", "acquire", "retry", "recover"}:
        raise _error(404, "Unknown synchronization operation")
    body = await _body(request, PasswordBody)
    return _response(
        await _runtime_call(request, user_id, action, local_id, password=_password(body))
    )


class RetentionBody(PasswordBody):
    revisions: list[str] = Field(min_length=1, max_length=64)
    acknowledge_cleanup: Literal[True]

    @field_validator("acknowledge_cleanup", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class CancelRetentionBody(PasswordBody):
    decision_id: str = Field(min_length=36, max_length=36)


@router.post("/sync/profiles/{local_id}/retention/list")
async def retention_list(request: Request, local_id: str):
    user_id = await _live_owner(request)
    body = await _body(request, PasswordBody)
    return _response(
        await _runtime_call(request, user_id, "retention_list", local_id, password=_password(body))
    )


@router.post("/sync/profiles/{local_id}/retention/schedule")
async def retention_schedule(request: Request, local_id: str):
    user_id = await _live_owner(request)
    body = await _body(request, RetentionBody)
    return _response(
        await _runtime_call(
            request,
            user_id,
            "retention_schedule",
            local_id,
            revisions=body.revisions,
            acknowledge_cleanup=body.acknowledge_cleanup,
            password=_password(body),
        )
    )


@router.post("/sync/profiles/{local_id}/retention/cancel")
async def retention_cancel(request: Request, local_id: str):
    user_id = await _live_owner(request)
    body = await _body(request, CancelRetentionBody)
    return _response(
        await _runtime_call(
            request,
            user_id,
            "retention_cancel",
            local_id,
            decision_id=body.decision_id,
            password=_password(body),
        )
    )
