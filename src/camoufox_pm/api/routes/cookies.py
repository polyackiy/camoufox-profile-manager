"""Deliberate cookie transfer; exports are sensitive attachment downloads."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from camoufox_pm.api.access import authorized_bytes_response, is_admin, require_profile_access
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.cookies import (
    CookieImportData,
    CookieImportRequest,
    CookiePreviewData,
    CookiePreviewRequest,
)
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.api.routes.recovery import get_backup_manager
from camoufox_pm.core.browser_session import BrowserLaunchError
from camoufox_pm.core.cookie_transfer import CookieTransfer, CookieTransferError

router = APIRouter()


async def service(profile_id: str) -> CookieTransfer:
    manager = get_profile_manager()
    if await manager.get_profile(profile_id) is None:
        raise HTTPException(404, "Profile not found")
    return CookieTransfer(manager, get_backup_manager())


async def _perform(operation):
    try:
        return await operation
    except CookieTransferError as exc:
        raise HTTPException(
            422,
            str(exc) if is_admin() else "Could not transfer cookies; verify the input and retry",
        ) from exc
    except BrowserLaunchError as exc:
        raise HTTPException(
            503, "Install the browser in Settings before transferring cookies"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            422, "Could not transfer cookies; verify the profile and retry"
        ) from exc


@router.post(
    "/profiles/{profile_id}/cookies/preview",
    response_model=ApiResponse[CookiePreviewData],
    operation_id="preview_cookie_import",
)
async def preview_cookies(profile_id: str, request: CookiePreviewRequest):
    await require_profile_access(profile_id, "edit")
    transfer = await service(profile_id)
    data = await _perform(transfer.preview(profile_id, request.text, request.format))
    return ApiResponse(success=True, message="Cookie input validated", data=data)


@router.post(
    "/profiles/{profile_id}/cookies/import",
    response_model=ApiResponse[CookieImportData],
    operation_id="import_profile_cookies",
)
async def import_cookies(profile_id: str, request: CookieImportRequest):
    await require_profile_access(profile_id, "edit")
    transfer = await service(profile_id)
    data = await _perform(
        transfer.import_cookies(
            profile_id,
            request.text,
            request.format,
            request.mode,
            request.row_version,
            request.confirm_replace,
        )
    )
    return ApiResponse(
        success=True,
        message="Cookies imported; session cookies apply at the next launch",
        data=data,
    )


@router.get(
    "/profiles/{profile_id}/cookies/export",
    operation_id="export_profile_cookies",
    responses={
        200: {
            "description": "Sensitive cookie attachment",
            "content": {"application/json": {}, "text/plain": {}},
        }
    },
)
async def export_cookies(
    profile_id: str,
    format: Literal["json", "netscape"] = Query("json"),
    row_version: int = Query(..., ge=0),
):
    await require_profile_access(profile_id, "export")
    transfer = await service(profile_id)
    text = await _perform(transfer.export_cookies(profile_id, format, row_version))
    suffix = "json" if format == "json" else "txt"
    return await authorized_bytes_response(
        text.encode(),
        [profile_id],
        media_type="application/json" if format == "json" else "text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="cookies.{suffix}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
