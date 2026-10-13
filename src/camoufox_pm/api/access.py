"""Request-scoped authorization and revocation-aware downloads."""

from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import StreamingResponse

from .principal import Principal

request_session_hash: ContextVar[str | None] = ContextVar("request_session_hash", default=None)

request_principal: ContextVar[Principal | None] = ContextVar("request_principal", default=None)


def current_principal() -> Principal:
    principal = request_principal.get()
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


def is_admin() -> bool:
    return current_principal().role == "administrator"


async def refreshed_principal(principal: Principal, session_hash: str | None = None) -> Principal:
    from .dependencies import get_storage_manager

    storage = get_storage_manager()
    if principal.kind == "session":
        if principal.username is None:
            raise HTTPException(status_code=403, detail="Access denied")
        session_hash = session_hash or request_session_hash.get()
        if session_hash is not None:
            session = await storage.get_session(session_hash)
            if (
                session is None
                or session["username"] != principal.username
                or datetime.fromisoformat(session["expires_at"]) <= datetime.now()
            ):
                raise HTTPException(status_code=403, detail="Authentication required")
        user = await storage.get_user_by_username(principal.username)
        if user is None:
            raise HTTPException(status_code=403, detail="Access denied")
        return Principal("session", username=principal.username, role=user["role"])
    if principal.kind == "anonymous_local" and await storage.count_users():
        raise HTTPException(status_code=403, detail="Authentication required")
    return principal


async def require_admin() -> None:
    if (await refreshed_principal(current_principal())).role != "administrator":
        raise HTTPException(status_code=403, detail="Administrator access required")


async def _check_profile(
    principal: Principal, profile_id: str, permission: str, session_hash: str | None = None
) -> None:
    from camoufox_pm.core.access import AccessControl

    from .dependencies import get_storage_manager

    principal = await refreshed_principal(principal, session_hash)
    if not await AccessControl(get_storage_manager()).can_profile(
        principal, profile_id, permission
    ):
        # The same response for an inaccessible and an unknown object.
        raise HTTPException(status_code=404, detail="Profile not found")


async def require_profile_access(profile_id: str, permission: str = "view") -> None:
    await _check_profile(current_principal(), profile_id, permission)


async def visible_profile_ids(permission: str = "view") -> set[str] | None:
    from camoufox_pm.core.access import AccessControl

    from .dependencies import get_storage_manager

    principal = await refreshed_principal(current_principal())
    return await AccessControl(get_storage_manager()).visible_profile_ids(principal, permission)


async def record_audit(action: str, object_id: str | None, result: str = "success") -> None:
    from .dependencies import get_storage_manager

    await get_storage_manager().append_audit(current_principal(), action, object_id, result)


async def authorized_file_response(
    path: str | Path, profile_ids, filename: str, media_type: str, background=None
):
    principal = current_principal()
    session_hash = request_session_hash.get()
    ids = tuple(profile_ids)
    for profile_id in ids:
        await _check_profile(principal, profile_id, "export", session_hash)
    if not ids:
        await require_admin()
    path = Path(path)

    async def chunks():
        with path.open("rb") as stream:
            while True:
                try:
                    for profile_id in ids:
                        await _check_profile(principal, profile_id, "export", session_hash)
                    if (
                        not ids
                        and (await refreshed_principal(principal, session_hash)).role
                        != "administrator"
                    ):
                        return
                except HTTPException:
                    return
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                yield chunk

    from urllib.parse import quote

    return StreamingResponse(
        chunks(),
        media_type=media_type,
        background=background,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(path.stat().st_size),
            "Cache-Control": "no-store",
        },
    )


async def authorized_bytes_response(content, profile_ids, media_type: str, headers=None):
    principal = current_principal()
    session_hash = request_session_hash.get()
    ids = tuple(profile_ids)
    for profile_id in ids:
        await _check_profile(principal, profile_id, "export", session_hash)
    if not ids:
        await require_admin()
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)

    async def chunks():
        for offset in range(0, len(data), 64 * 1024):
            try:
                for profile_id in ids:
                    await _check_profile(principal, profile_id, "export", session_hash)
                if (
                    not ids
                    and (await refreshed_principal(principal, session_hash)).role != "administrator"
                ):
                    return
            except HTTPException:
                return
            yield data[offset : offset + 64 * 1024]

    return StreamingResponse(
        chunks(),
        media_type=media_type,
        headers={**(headers or {}), "Content-Length": str(len(data)), "Cache-Control": "no-store"},
    )


def profile_permissions(profile_id: str) -> dict[str, bool]:
    """Presentation hints; operations always recheck the live policy separately."""
    from .dependencies import get_storage_manager

    principal = current_principal()
    if principal.kind != "session":
        return dict.fromkeys(("view", "edit", "launch", "export"), True)
    connection = get_storage_manager().db._connection
    user = connection.execute(
        "SELECT role FROM users WHERE username=?", (principal.username,)
    ).fetchone()
    if user is None:
        return dict.fromkeys(("view", "edit", "launch", "export"), False)
    if user["role"] == "administrator":
        return dict.fromkeys(("view", "edit", "launch", "export"), True)
    grant = connection.execute(
        "SELECT can_export FROM profile_grants WHERE username=? AND profile_id=?",
        (principal.username, profile_id),
    ).fetchone()
    return {
        "view": grant is not None,
        "edit": grant is not None and user["role"] == "operator",
        "launch": grant is not None and user["role"] == "operator",
        "export": grant is not None and bool(grant["can_export"]),
    }
