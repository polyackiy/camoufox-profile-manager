"""Local XPI uploads and managed profile snapshots; never remote downloads."""

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from camoufox_pm.api.access import (
    is_admin,
    require_admin,
    require_profile_access,
)
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.extensions import ExtensionAssignmentRequest
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.core.extension_library import MAX_UPLOAD_BYTES, ExtensionLibrary

router = APIRouter()


async def perform(operation):
    try:
        return await operation
    except LookupError:
        raise HTTPException(404, "Extension or profile not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc) if is_admin() else "Invalid extension request") from None


@router.get(
    "/extensions", response_model=ApiResponse[dict[str, Any]], operation_id="list_extensions"
)
async def list_extensions():
    await require_admin()
    return ApiResponse(
        success=True,
        data={"extensions": await ExtensionLibrary(get_profile_manager()).list()},
        message="Extension library",
    )


@router.post(
    "/extensions",
    status_code=201,
    response_model=ApiResponse[dict[str, Any]],
    operation_id="upload_extension",
)
async def upload_extension(file: UploadFile = File(...)):
    await require_admin()
    payload = bytearray()
    try:
        while chunk := await file.read(65536):
            payload.extend(chunk)
            if len(payload) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Extension upload exceeds 16 MB")
    finally:
        await file.close()
    data = await perform(ExtensionLibrary(get_profile_manager()).upload(bytes(payload)))
    return ApiResponse(success=True, data=data, message="Extension uploaded")


@router.delete(
    "/extensions/{extension_id}", response_model=ApiResponse[None], operation_id="delete_extension"
)
async def delete_extension(extension_id: str, row_version: int = Query(..., ge=0)):
    await require_admin()
    await perform(ExtensionLibrary(get_profile_manager()).delete(extension_id, row_version))
    return ApiResponse(
        success=True, data=None, message="Extension deleted; profiles keep their snapshot"
    )


@router.get(
    "/profiles/{profile_id}/extensions",
    response_model=ApiResponse[dict[str, Any]],
    operation_id="profile_extensions",
)
async def profile_extensions(profile_id: str):
    await require_profile_access(profile_id)
    return ApiResponse(
        success=True,
        data=await perform(ExtensionLibrary(get_profile_manager()).profile_extensions(profile_id)),
        message="Profile extensions",
    )


@router.put(
    "/profiles/{profile_id}/extensions",
    response_model=ApiResponse[dict[str, Any]],
    operation_id="assign_profile_extensions",
)
async def assign_profile_extensions(profile_id: str, request: ExtensionAssignmentRequest):
    await require_profile_access(profile_id, "edit")
    return ApiResponse(
        success=True,
        data=await perform(
            ExtensionLibrary(get_profile_manager()).assign(
                profile_id,
                request.extension_ids,
                request.row_version,
                retained_refs=request.retained_refs,
            )
        ),
        message="Profile extension snapshots saved",
    )
