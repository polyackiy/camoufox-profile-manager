"""Reusable profile settings with independent extension snapshots."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from camoufox_pm.api.access import (
    require_admin,
)
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.profiles import ProfileResponse
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.api.models.templates import (
    TemplateCreateRequest,
    TemplateFromProfileRequest,
    TemplateProfileRequest,
    TemplateUpdateRequest,
)
from camoufox_pm.core.profile_templates import ProfileTemplates

router = APIRouter()


async def perform(operation):
    try:
        return await operation
    except LookupError:
        raise HTTPException(404, "Template or profile not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get(
    "/templates", response_model=ApiResponse[dict[str, Any]], operation_id="list_profile_templates"
)
async def list_templates():
    await require_admin()
    return ApiResponse(
        success=True,
        data={"templates": await ProfileTemplates(get_profile_manager()).list()},
        message="Profile templates",
    )


@router.post(
    "/templates",
    status_code=201,
    response_model=ApiResponse[dict[str, Any]],
    operation_id="create_profile_template",
)
async def create_template(request: TemplateCreateRequest):
    await require_admin()
    data = await perform(
        ProfileTemplates(get_profile_manager()).create(**request.model_dump(mode="json"))
    )
    return ApiResponse(success=True, data=data, message="Template saved")


@router.post(
    "/templates/from-profile",
    status_code=201,
    response_model=ApiResponse[dict[str, Any]],
    operation_id="template_from_profile",
)
async def template_from_profile(request: TemplateFromProfileRequest):
    await require_admin()
    data = await perform(
        ProfileTemplates(get_profile_manager()).from_profile(**request.model_dump())
    )
    return ApiResponse(success=True, data=data, message="Profile settings saved as template")


@router.patch(
    "/templates/{template_id}",
    response_model=ApiResponse[dict[str, Any]],
    operation_id="update_profile_template",
)
async def update_template(template_id: str, request: TemplateUpdateRequest):
    await require_admin()
    changes = request.model_dump(mode="json", exclude_unset=True, exclude={"row_version"})
    data = await perform(
        ProfileTemplates(get_profile_manager()).update(template_id, request.row_version, changes)
    )
    return ApiResponse(success=True, data=data, message="Template updated")


@router.delete(
    "/templates/{template_id}",
    response_model=ApiResponse[None],
    operation_id="delete_profile_template",
)
async def delete_template(template_id: str, row_version: int = Query(..., ge=0)):
    await require_admin()
    await perform(ProfileTemplates(get_profile_manager()).delete(template_id, row_version))
    return ApiResponse(
        success=True, data=None, message="Template deleted; existing profiles are unchanged"
    )


@router.post(
    "/templates/{template_id}/profiles",
    status_code=201,
    response_model=ApiResponse[ProfileResponse],
    operation_id="create_profile_from_template",
)
async def profile_from_template(template_id: str, request: TemplateProfileRequest):
    await require_admin()
    profile = await perform(
        ProfileTemplates(get_profile_manager()).create_profile(template_id, **request.model_dump())
    )
    return ApiResponse(
        success=True,
        data=ProfileResponse.from_profile(profile),
        message="Fresh profile created from template",
    )
