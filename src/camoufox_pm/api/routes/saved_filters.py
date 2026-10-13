"""Saved profile views shared by authenticated users of this instance."""

from fastapi import APIRouter, HTTPException, Query

from camoufox_pm.api.access import (
    require_admin,
)
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.saved_filters import (
    SavedFilterCreate,
    SavedFilterList,
    SavedFilterUpdate,
)
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.core.saved_filters import SavedFilter, SavedFilterStore

router = APIRouter()


def store() -> SavedFilterStore:
    return SavedFilterStore(get_profile_manager().storage)


@router.get(
    "/saved-filters", response_model=ApiResponse[SavedFilterList], operation_id="list_saved_filters"
)
async def list_filters():
    await require_admin()
    return ApiResponse(
        success=True, message="Saved view operation completed", data={"filters": store().list()}
    )


@router.post(
    "/saved-filters",
    response_model=ApiResponse[SavedFilter],
    status_code=201,
    operation_id="create_saved_filter",
)
async def create_filter(request: SavedFilterCreate):
    await require_admin()
    return ApiResponse(
        success=True,
        message="Saved view operation completed",
        data=store().create(request.name, request.config),
    )


@router.patch(
    "/saved-filters/{filter_id}",
    response_model=ApiResponse[SavedFilter],
    operation_id="update_saved_filter",
)
async def update_filter(filter_id: str, request: SavedFilterUpdate):
    await require_admin()
    result = store().update(
        filter_id, request.row_version, name=request.name, config=request.config
    )
    if result is None:
        raise HTTPException(404, "Saved view not found")
    return ApiResponse(success=True, message="Saved view operation completed", data=result)


@router.delete(
    "/saved-filters/{filter_id}",
    response_model=ApiResponse[None],
    operation_id="delete_saved_filter",
)
async def delete_filter(filter_id: str, row_version: int = Query(..., ge=0)):
    await require_admin()
    if not store().delete(filter_id, row_version):
        raise HTTPException(404, "Saved view not found")
    return ApiResponse(success=True, message="Saved view operation completed", data=None)
