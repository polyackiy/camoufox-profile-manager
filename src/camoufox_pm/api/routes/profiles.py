"""
API routes for managing profiles.
"""

import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from loguru import logger

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.profiles import (
    ProfileCloneRequest,
    ProfileCreateRequest,
    ProfileLaunchRequest,
    ProfileLaunchResponse,
    ProfileListResponse,
    ProfileResponse,
    ProfileStatsResponse,
    ProfileUpdateRequest,
)
from camoufox_pm.api.models.system import ApiResponse
from camoufox_pm.core.excel_manager import ExcelManager
from camoufox_pm.core.models import ProfileStatus

router = APIRouter()


@router.get(
    "/profiles",
    response_model=ProfileListResponse,
    summary="List profiles",
    description="List all profiles with filtering and pagination",
)
async def list_profiles(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Profiles per page"),
    status: ProfileStatus | None = Query(None, description="Filter by status"),
    group: str | None = Query(None, description="Filter by group"),
    search: str | None = Query(None, description="Search by name"),
):
    """List profiles with filtering."""
    try:
        profile_manager = get_profile_manager()

        # Build the filters
        filters: dict[str, Any] = {}
        if status:
            filters["status"] = status
        if group:
            filters["group"] = group
        if search:
            filters["name_like"] = search

        # Fetch the profiles
        profiles = await profile_manager.list_profiles(filters=filters)

        # Pagination
        total = len(profiles)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_profiles = profiles[start_idx:end_idx]

        # Convert to API models
        profile_responses = []
        for profile in paginated_profiles:
            profile_responses.append(
                ProfileResponse(
                    id=profile.id,
                    name=profile.name,
                    group=profile.group,
                    status=profile.status,
                    browser_settings=profile.browser_settings.model_dump()
                    if profile.browser_settings
                    else {},
                    proxy_config=profile.proxy.model_dump() if profile.proxy else None,
                    storage_path=profile.storage_path,
                    notes=profile.notes,
                    created_at=profile.created_at,
                    updated_at=profile.updated_at,
                    last_used=profile.last_used,
                )
            )

        return ProfileListResponse(
            profiles=profile_responses,
            total=total,
            page=page,
            per_page=per_page,
            has_next=end_idx < total,
            has_prev=page > 1,
        )

    except Exception as e:
        logger.error(f"Failed to list profiles: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/profiles",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a profile",
    description="Create a new profile with an auto-generated fingerprint",
)
async def create_profile(request: ProfileCreateRequest):
    """Create a new profile."""
    try:
        profile_manager = get_profile_manager()

        # Create the profile
        profile = await profile_manager.create_profile(
            name=request.name,
            group=request.group,
            browser_settings=request.browser_settings,
            proxy_config=request.proxy_config,
            generate_fingerprint=request.generate_fingerprint,
        )

        logger.info(f"Created profile: {profile.name} (ID: {profile.id})")

        return ProfileResponse(
            id=profile.id,
            name=profile.name,
            group=profile.group,
            status=profile.status,
            browser_settings=profile.browser_settings.model_dump() if profile.browser_settings else {},
            proxy_config=profile.proxy.model_dump() if profile.proxy else None,
            storage_path=profile.storage_path,
            notes=profile.notes,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            last_used=profile.last_used,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to create profile: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/profiles/{profile_id}",
    response_model=ProfileResponse,
    summary="Get a profile",
    description="Get detailed information about a profile",
)
async def get_profile(profile_id: str):
    """Get a profile by ID."""
    try:
        profile_manager = get_profile_manager()
        profile = await profile_manager.get_profile(profile_id)

        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile with ID {profile_id} not found")

        return ProfileResponse(
            id=profile.id,
            name=profile.name,
            group=profile.group,
            status=profile.status,
            browser_settings=profile.browser_settings.model_dump() if profile.browser_settings else {},
            proxy_config=profile.proxy.model_dump() if profile.proxy else None,
            storage_path=profile.storage_path,
            notes=profile.notes,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            last_used=profile.last_used,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put(
    "/profiles/{profile_id}",
    response_model=ProfileResponse,
    summary="Update a profile",
    description="Update a profile's data",
)
async def update_profile(profile_id: str, request: ProfileUpdateRequest):
    """Update a profile."""
    try:
        profile_manager = get_profile_manager()

        # Build the updates
        updates: dict[str, Any] = {}
        if request.name is not None:
            updates["name"] = request.name
        if request.group is not None:
            updates["group"] = request.group
        if request.status is not None:
            updates["status"] = request.status
        if request.browser_settings is not None:
            updates["browser_settings"] = request.browser_settings
        if request.proxy_config is not None:
            updates["proxy_config"] = request.proxy_config
        if request.notes is not None:
            updates["notes"] = request.notes

        # Handle individual browser settings
        browser_updates: dict[str, Any] = {}
        if request.browser_os is not None:
            browser_updates["os"] = request.browser_os
        if request.browser_screen is not None:
            browser_updates["screen"] = request.browser_screen
        if request.browser_user_agent is not None:
            browser_updates["user_agent"] = request.browser_user_agent
        if request.browser_languages is not None:
            browser_updates["languages"] = request.browser_languages
        if request.browser_timezone is not None:
            browser_updates["timezone"] = request.browser_timezone
        if request.browser_locale is not None:
            browser_updates["locale"] = request.browser_locale
        if request.browser_webrtc_mode is not None:
            browser_updates["webrtc_mode"] = request.browser_webrtc_mode
        if request.browser_canvas_noise is not None:
            browser_updates["canvas_noise"] = request.browser_canvas_noise
        if request.browser_webgl_noise is not None:
            browser_updates["webgl_noise"] = request.browser_webgl_noise
        if request.browser_audio_noise is not None:
            browser_updates["audio_noise"] = request.browser_audio_noise
        if request.browser_hardware_concurrency is not None:
            browser_updates["hardware_concurrency"] = request.browser_hardware_concurrency
        if request.browser_device_memory is not None:
            browser_updates["device_memory"] = request.browser_device_memory
        if request.browser_max_touch_points is not None:
            browser_updates["max_touch_points"] = request.browser_max_touch_points
        if request.browser_window_width is not None:
            browser_updates["window_width"] = request.browser_window_width
        if request.browser_window_height is not None:
            browser_updates["window_height"] = request.browser_window_height

        # Merge browser settings updates if any
        if browser_updates:
            # Load the current profile to merge browser settings
            current_profile = await profile_manager.get_profile(profile_id)
            if current_profile and current_profile.browser_settings:
                current_settings = current_profile.browser_settings.model_dump()
                current_settings.update(browser_updates)
                updates["browser_settings"] = current_settings

        # Apply the update
        updated_profile = await profile_manager.update_profile(profile_id, updates)

        if not updated_profile:
            raise HTTPException(status_code=404, detail=f"Profile with ID {profile_id} not found")

        logger.info(f"Updated profile: {updated_profile.name} (ID: {profile_id})")

        return ProfileResponse(
            id=updated_profile.id,
            name=updated_profile.name,
            group=updated_profile.group,
            status=updated_profile.status,
            browser_settings=updated_profile.browser_settings.model_dump()
            if updated_profile.browser_settings
            else {},
            proxy_config=updated_profile.proxy.model_dump() if updated_profile.proxy else None,
            storage_path=updated_profile.storage_path,
            notes=updated_profile.notes,
            created_at=updated_profile.created_at,
            updated_at=updated_profile.updated_at,
            last_used=updated_profile.last_used,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete(
    "/profiles/{profile_id}",
    response_model=ApiResponse,
    summary="Delete a profile",
    description="Delete a profile and all associated data",
)
async def delete_profile(profile_id: str):
    """Delete a profile."""
    try:
        profile_manager = get_profile_manager()
        success = await profile_manager.delete_profile(profile_id)

        if not success:
            raise HTTPException(status_code=404, detail=f"Profile with ID {profile_id} not found")

        logger.info(f"Deleted profile: ID {profile_id}")

        return ApiResponse(success=True, message=f"Profile {profile_id} deleted successfully", data=None)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/profiles/{profile_id}/launch",
    response_model=ProfileLaunchResponse,
    summary="Launch browser",
    description="Launch Camoufox with the profile's settings",
)
async def launch_profile(profile_id: str, request: ProfileLaunchRequest):
    """Launch a browser with a profile."""
    try:
        profile_manager = get_profile_manager()

        # Launch the browser
        browser_session = await profile_manager.launch_browser(
            profile_id,
            headless=request.headless,
            window_size=request.window_size,
            **request.additional_options or {},
        )

        logger.info(f"Launched browser for profile: {profile_id}")

        return ProfileLaunchResponse(
            profile_id=profile_id,
            browser_session_id=str(uuid.uuid4()),
            status=browser_session.get("status", "launched"),
            message=browser_session.get("message", "Browser launched successfully"),
            camoufox_options={
                "process_id": browser_session.get("process_id"),
                "status": browser_session.get("status"),
                "options": browser_session.get("camoufox_options", {}),
            },
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to launch browser for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/profiles/{profile_id}/clone",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Clone a profile",
    description="Create a copy of a profile with a new fingerprint",
)
async def clone_profile(profile_id: str, request: ProfileCloneRequest):
    """Clone a profile."""
    try:
        profile_manager = get_profile_manager()

        # Clone the profile
        cloned_profile = await profile_manager.clone_profile(
            profile_id, request.new_name, regenerate_fingerprint=request.regenerate_fingerprint
        )
        if not cloned_profile:
            raise HTTPException(status_code=404, detail=f"Profile with ID {profile_id} not found")

        logger.info(f"Cloned profile: {profile_id} -> {cloned_profile.id}")

        return ProfileResponse(
            id=cloned_profile.id,
            name=cloned_profile.name,
            group=cloned_profile.group,
            status=cloned_profile.status,
            browser_settings=cloned_profile.browser_settings.model_dump()
            if cloned_profile.browser_settings
            else {},
            proxy_config=cloned_profile.proxy.model_dump() if cloned_profile.proxy else None,
            storage_path=cloned_profile.storage_path,
            notes=cloned_profile.notes,
            created_at=cloned_profile.created_at,
            updated_at=cloned_profile.updated_at,
            last_used=cloned_profile.last_used,
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to clone profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/profiles/{profile_id}/stats",
    response_model=ProfileStatsResponse,
    summary="Get profile statistics",
    description="Get usage statistics for a profile",
)
async def get_profile_stats(profile_id: str):
    """Get usage statistics for a profile."""
    try:
        profile_manager = get_profile_manager()

        # Verify the profile exists
        profile = await profile_manager.get_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile with ID {profile_id} not found")

        # Fetch the statistics
        stats = await profile_manager.get_profile_stats(profile_id)

        return ProfileStatsResponse(
            profile_id=profile_id,
            total_sessions=stats.get("total_sessions", 0),
            total_duration_minutes=stats.get("total_duration_minutes", 0),
            last_session=stats.get("last_session"),
            success_rate=stats.get("success_rate", 0.0),
            actions=stats.get("actions", []),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get statistics for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/profiles/{profile_id}/reset-fingerprint",
    response_model=ProfileResponse,
    summary="Reset fingerprint",
    description="Fully regenerate the browser fingerprint for a profile",
)
async def reset_profile_fingerprint(profile_id: str):
    """Reset and regenerate a profile's fingerprint."""
    try:
        profile_manager = get_profile_manager()

        # Verify the profile exists
        profile = await profile_manager.get_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile with ID {profile_id} not found")

        # Regenerate the fingerprint
        updated_profile = await profile_manager.rotate_profile_fingerprint(profile_id)
        if not updated_profile:
            raise HTTPException(status_code=404, detail=f"Profile with ID {profile_id} not found")

        logger.info(f"Reset fingerprint for profile: {updated_profile.name} (ID: {profile_id})")

        return ProfileResponse(
            id=updated_profile.id,
            name=updated_profile.name,
            group=updated_profile.group,
            status=updated_profile.status,
            browser_settings=updated_profile.browser_settings.model_dump()
            if updated_profile.browser_settings
            else {},
            proxy_config=updated_profile.proxy.model_dump() if updated_profile.proxy else None,
            storage_path=updated_profile.storage_path,
            notes=updated_profile.notes,
            created_at=updated_profile.created_at,
            updated_at=updated_profile.updated_at,
            last_used=updated_profile.last_used,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reset fingerprint for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/profiles/export/excel",
    summary="Export profiles to Excel",
    description="Export all profiles to an Excel file for bulk editing",
)
async def export_profiles_to_excel():
    """Export all profiles to an Excel file."""
    try:
        profile_manager = get_profile_manager()
        excel_manager = ExcelManager(profile_manager)

        # Export the profiles
        excel_data = await excel_manager.export_profiles_to_excel()

        logger.info("Profiles exported to Excel")

        return Response(
            content=excel_data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=camoufox_profiles.xlsx"},
        )

    except Exception as e:
        logger.error(f"Failed to export profiles to Excel: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/profiles/import/excel",
    response_model=ApiResponse,
    summary="Import profiles from Excel",
    description="Import profiles from an Excel file, creating new ones",
)
async def import_profiles_from_excel(file: UploadFile = File(...)):
    """Import profiles from an Excel file."""
    try:
        # Validate the file type
        if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
            raise HTTPException(
                status_code=400, detail="Only Excel files (.xlsx, .xls) are supported"
            )

        # Read the file
        excel_data = await file.read()

        profile_manager = get_profile_manager()
        excel_manager = ExcelManager(profile_manager)

        # Import the profiles
        result = await excel_manager.import_profiles_from_excel(excel_data)

        if result["success"]:
            logger.info(
                f"Import complete: created {result['created_count']}, updated {result['updated_count']}"
            )
        else:
            logger.warning(f"Import finished with {result['error_count']} errors")

        return ApiResponse(
            success=result["success"],
            message=result["summary"],
            data={
                "created_count": result["created_count"],
                "updated_count": result["updated_count"],
                "error_count": result["error_count"],
                "errors": result["errors"],
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to import profiles from Excel: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/profiles/{profile_id}/close",
    summary="Close browser",
    description="Force-close the browser for a profile",
)
async def close_profile_browser(profile_id: str):
    """Close the browser for a profile."""
    try:
        profile_manager = get_profile_manager()
        result = await profile_manager.close_browser(profile_id)

        logger.info(f"Browser closed for profile: {profile_id}")
        return result

    except Exception as e:
        logger.error(f"Failed to close browser for profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/browsers/active", summary="List active browsers", description="List all active browsers"
)
async def get_active_browsers():
    """List active browsers."""
    try:
        profile_manager = get_profile_manager()
        active_browsers = await profile_manager.get_active_browsers()

        return {"active_browsers": active_browsers, "count": len(active_browsers)}

    except Exception as e:
        logger.error(f"Failed to list active browsers: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/browsers/close-all",
    summary="Close all browsers",
    description="Force-close all active browsers",
)
async def close_all_browsers():
    """Close all active browsers."""
    try:
        profile_manager = get_profile_manager()
        result = await profile_manager.close_all_browsers()

        logger.info(f"Closed {result['closed_count']} browsers")
        return result

    except Exception as e:
        logger.error(f"Failed to close all browsers: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
