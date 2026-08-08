"""API routes for managing profile groups."""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.groups import (
    GroupCreateRequest,
    GroupListResponse,
    GroupResponse,
    GroupUpdateRequest,
)
from camoufox_pm.api.models.system import ApiResponse

router = APIRouter()


@router.get(
    "/groups",
    response_model=GroupListResponse,
    operation_id="list_groups",
    summary="List groups.",
    description="List all profile groups. Groups are few, so the list is not paginated.",
)
async def list_groups():
    """List all groups."""
    try:
        profile_manager = get_profile_manager()
        groups_data = await profile_manager.list_groups()
        groups = [
            GroupResponse(
                id=group_data["id"],
                name=group_data["name"],
                description=group_data.get("description"),
                profile_count=group_data.get("profile_count", 0),
                created_at=group_data["created_at"],
            )
            for group_data in groups_data
        ]
        return GroupListResponse(groups=groups, total=len(groups))
    except Exception as exc:
        logger.error(f"Failed to list groups: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/groups",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_group",
    summary="Create a group.",
    description="Create a new profile group.",
)
async def create_group(request: GroupCreateRequest):
    """Create a new group."""
    try:
        profile_manager = get_profile_manager()
        group_data = await profile_manager.create_group(
            name=request.name, description=request.description
        )
        logger.info(f"Created group: {request.name}")
        return GroupResponse(
            id=group_data["id"],
            name=group_data["name"],
            description=group_data.get("description"),
            profile_count=0,
            created_at=group_data["created_at"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Failed to create group: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/groups/{group_id}",
    response_model=GroupResponse,
    operation_id="get_group",
    summary="Get a group.",
    description="Get information about a group.",
)
async def get_group(group_id: str):
    """Get a group by ID."""
    try:
        profile_manager = get_profile_manager()
        group_data = await profile_manager.get_group(group_id)
        if not group_data:
            raise HTTPException(status_code=404, detail=f"Group with ID {group_id} not found")
        return GroupResponse(
            id=group_data["id"],
            name=group_data["name"],
            description=group_data.get("description"),
            profile_count=group_data.get("profile_count", 0),
            created_at=group_data["created_at"],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to get group {group_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put(
    "/groups/{group_id}",
    response_model=GroupResponse,
    operation_id="update_group",
    summary="Update a group.",
    description="Update a group's data.",
)
async def update_group(group_id: str, request: GroupUpdateRequest):
    """Update a group."""
    try:
        profile_manager = get_profile_manager()
        updates = {}
        if request.name is not None:
            updates["name"] = request.name
        if request.description is not None:
            updates["description"] = request.description

        group_data = await profile_manager.update_group(group_id, updates)
        if not group_data:
            raise HTTPException(status_code=404, detail=f"Group with ID {group_id} not found")
        logger.info("Updated group: {} (ID: {})".format(group_data["name"], group_id))
        return GroupResponse(
            id=group_data["id"],
            name=group_data["name"],
            description=group_data.get("description"),
            profile_count=group_data.get("profile_count", 0),
            created_at=group_data["created_at"],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to update group {group_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete(
    "/groups/{group_id}",
    response_model=ApiResponse[None],
    operation_id="delete_group",
    summary="Delete a group.",
    description="Delete a group; its profiles become ungrouped.",
)
async def delete_group(group_id: str):
    """Delete a group."""
    try:
        profile_manager = get_profile_manager()
        success = await profile_manager.delete_group(group_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Group with ID {group_id} not found")
        logger.info(f"Deleted group: ID {group_id}")
        return ApiResponse(
            success=True, message=f"Group {group_id} deleted successfully", data=None
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to delete group {group_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
