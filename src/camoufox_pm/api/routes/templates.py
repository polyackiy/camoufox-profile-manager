"""Reusable profile settings with independent extension snapshots."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from camoufox_pm.api.access import (
    record_audit,
    require_admin,
)
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.profiles import ProfileResponse
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.api.models.templates import (
    TemplateBatchRequest,
    TemplateBatchResponse,
    TemplateCreateRequest,
    TemplateFromProfileRequest,
    TemplateProfileRequest,
    TemplateUpdateRequest,
)
from camoufox_pm.core.profile_templates import ProfileTemplates
from camoufox_pm.core.proxy_inventory import ProxyCredentialsUnavailable
from camoufox_pm.core.template_batches import BatchAccessLost, BatchConflict, TemplateBatches

router = APIRouter()


async def batch_authority():
    try:
        await require_admin()
    except HTTPException:
        raise BatchAccessLost from None


@router.get(
    "/template-profile-batches",
    response_model=ApiResponse[dict[str, Any]],
    operation_id="list_template_profile_batches",
)
async def list_template_batches(response: Response):
    await require_admin()
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(
        success=True,
        data={"batches": TemplateBatches(get_profile_manager()).list()},
        message="Recent profile batches",
    )


@router.get(
    "/template-profile-batches/{batch_id}",
    response_model=ApiResponse[dict[str, Any]],
    operation_id="get_template_profile_batch",
)
async def get_template_batch(batch_id: UUID, response: Response):
    await require_admin()
    response.headers["Cache-Control"] = "no-store"
    try:
        result = TemplateBatches(get_profile_manager()).get(str(batch_id))
    except LookupError:
        raise HTTPException(404, "Batch not found") from None
    return ApiResponse(success=True, data=result, message="Profile batch")


@router.post(
    "/templates/{template_id}/batch-preview",
    response_model=ApiResponse[dict[str, Any]],
    operation_id="preview_template_profile_batch",
)
async def preview_template_batch(
    template_id: str, request: TemplateBatchRequest, response: Response
):
    await require_admin()
    response.headers["Cache-Control"] = "no-store"
    result = await perform_preview(template_id, request)
    return ApiResponse(success=True, data=result, message="Profile batch preview")


async def perform_preview(template_id, request):
    try:
        return TemplateBatches(get_profile_manager()).preview(
            template_id, request.model_dump(mode="json")
        )
    except LookupError:
        raise HTTPException(404, "Template not found") from None
    except ProxyCredentialsUnavailable:
        raise HTTPException(
            409, "Stored proxy credentials are unavailable. Restore the manager encryption key."
        ) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post(
    "/templates/{template_id}/profile-batches",
    response_model=TemplateBatchResponse,
    operation_id="create_template_profile_batch",
)
async def create_template_batch(
    template_id: str, request: TemplateBatchRequest, response: Response
):
    await require_admin()
    response.headers["Cache-Control"] = "no-store"

    async def created(profile_id):
        await record_audit("create_profile_from_template_batch", profile_id)

    try:
        result = await TemplateBatches(get_profile_manager()).submit(
            template_id,
            request.model_dump(mode="json"),
            authorize=batch_authority,
            on_created=created,
        )
    except BatchAccessLost:
        raise HTTPException(403, "Administrator access required") from None
    except BatchConflict:
        raise HTTPException(409, "This batch identifier belongs to a different preview") from None
    except ProxyCredentialsUnavailable:
        raise HTTPException(
            409, "Stored proxy credentials are unavailable. Restore the manager encryption key."
        ) from None
    except LookupError:
        raise HTTPException(404, "Template not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return TemplateBatchResponse(
        success=True,
        data=result,
        errors=[item["position"] for item in result["items"] if item["status"] != "created"],
        message="Profile batch processed",
    )


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
