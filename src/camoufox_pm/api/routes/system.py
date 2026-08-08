"""API routes for system functions."""

import time
from pathlib import Path

import psutil
from fastapi import APIRouter, HTTPException
from loguru import logger

from camoufox_pm import __version__
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.system import (
    ApiResponse,
    DevicePreset,
    PresetListData,
    ProfileCleanupResponse,
    ProfileDiagnosticResponse,
    SystemConfigData,
    SystemInfoData,
    SystemStatusResponse,
)
from camoufox_pm.config import get_settings
from camoufox_pm.core import fingerprint_store
from camoufox_pm.core.browser_session import CAMOUFOX_AVAILABLE
from camoufox_pm.core.cleanup import ProfileCleanupManager

router = APIRouter()

# Application startup time, used to compute uptime.
startup_time = time.time()


@router.get(
    "/system/status",
    response_model=SystemStatusResponse,
    operation_id="get_system_status",
    summary="Get the system status.",
    description="Get an overview of the system state.",
)
async def get_system_status():
    """Return an overview of the system state."""
    try:
        profile_manager = get_profile_manager()
        profiles = await profile_manager.list_profiles()
        active_profiles = len([p for p in profiles if p.status == "active"])
        active_browsers = await profile_manager.get_active_browsers()
        groups = await profile_manager.list_groups()

        return SystemStatusResponse(
            total_profiles=len(profiles),
            active_profiles=active_profiles,
            running_browsers=len(active_browsers),
            total_groups=len(groups),
            system_load=psutil.getloadavg()[0] if hasattr(psutil, "getloadavg") else 0.0,
            memory_usage=psutil.virtual_memory().percent,
            disk_usage=psutil.disk_usage("/").percent,
            uptime_seconds=int(time.time() - startup_time),
        )
    except Exception as exc:
        logger.error(f"Failed to get system status: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/system/profiles/diagnostic",
    response_model=ProfileDiagnosticResponse,
    operation_id="diagnose_profiles",
    summary="Diagnose profile storage.",
    description="Check profile storage health and find issues.",
)
async def diagnostic_profiles():
    """Diagnose profile storage health."""
    try:
        cleanup_manager = ProfileCleanupManager()
        await cleanup_manager.initialize()
        try:
            result = await cleanup_manager.full_diagnostic()
            return ProfileDiagnosticResponse(
                total_profiles_in_db=result["total_profiles_in_db"],
                total_directories_on_disk=result["total_directories_on_disk"],
                total_disk_size_mb=result["total_disk_size_mb"],
                orphaned_directories=result["orphaned_directories"],
                orphaned_size_mb=result["orphaned_size_mb"],
                missing_directories=result["missing_directories"],
                healthy_profiles=result["healthy_profiles"],
                issues_found=result["issues_found"],
            )
        finally:
            await cleanup_manager.close()
    except Exception as exc:
        logger.error(f"Failed to diagnose profiles: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/system/profiles/cleanup",
    response_model=ProfileCleanupResponse,
    operation_id="cleanup_orphaned_profiles",
    summary="Clean up orphaned profiles.",
    description="Remove profile directories that are not present in the database.",
)
async def cleanup_orphaned_profiles(dry_run: bool = False):
    """Clean up orphaned profile directories."""
    try:
        cleanup_manager = ProfileCleanupManager()
        await cleanup_manager.initialize()
        try:
            if dry_run:
                orphaned = await cleanup_manager.find_orphaned_profile_directories()
                missing = await cleanup_manager.find_missing_profile_directories()
                return ProfileCleanupResponse(
                    orphaned_removed=len(orphaned),
                    directories_created=len(missing),
                    freed_space_mb=sum(item["size_mb"] for item in orphaned),
                    dry_run=True,
                    message=f"Dry run: would remove {len(orphaned)} directories, create {len(missing)}",
                )
            results = await cleanup_manager.auto_cleanup(dry_run=False)
            freed_space = results["orphaned_removed"] * 30  # rough estimate; files already removed
            return ProfileCleanupResponse(
                orphaned_removed=results["orphaned_removed"],
                directories_created=results["directories_created"],
                freed_space_mb=freed_space,
                dry_run=False,
                message=(
                    f"Cleanup complete: removed {results['orphaned_removed']}, "
                    f"created {results['directories_created']}"
                ),
            )
        finally:
            await cleanup_manager.close()
    except Exception as exc:
        logger.error(f"Failed to clean up profiles: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/system/restart",
    response_model=ApiResponse[None],
    operation_id="restart_system",
    summary="Prepare for a restart.",
    description="Close all browsers as part of a safe restart. Does not restart the process itself.",
)
async def restart_system():
    """Close all browsers in preparation for a restart."""
    try:
        profile_manager = get_profile_manager()
        await profile_manager.close_all_browsers()
        logger.info("System restart requested")
        return ApiResponse(
            success=True, message="Browsers closed; restart the process to continue", data=None
        )
    except Exception as exc:
        logger.error(f"Failed to restart system: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/fingerprints/presets",
    response_model=ApiResponse[PresetListData],
    operation_id="list_fingerprint_presets",
    summary="List real device presets.",
    description=(
        "Fingerprints captured from real machines, bundled with Camoufox. A profile "
        "created from one is pinned to that device instead of a generated fingerprint."
    ),
)
async def list_fingerprint_presets(
    os: str | None = None,
):
    """List the available real device fingerprint presets."""
    presets = fingerprint_store.list_presets(os)
    return ApiResponse(
        success=True,
        message=f"{len(presets)} presets available",
        data=PresetListData(
            presets=[DevicePreset(**preset) for preset in presets], total=len(presets)
        ),
    )


@router.get(
    "/system/config",
    response_model=ApiResponse[SystemConfigData],
    operation_id="get_system_config",
    summary="Report the effective configuration.",
    description="Report how this instance is configured. Never returns secret values.",
)
async def get_system_config():
    """Return the effective configuration, reporting secrets only as set/unset.

    This backs the Settings screen so users can see whether encryption and the
    API key are on without reading environment variables.
    """
    settings = get_settings()
    return ApiResponse(
        success=True,
        message="Effective configuration",
        data=SystemConfigData(
            version=__version__,
            host=settings.host,
            port=settings.port,
            database_path=str(Path(settings.db_path).resolve()),
            api_key_set=bool(settings.api_key),
            encryption_enabled=bool(settings.secret_key),
            cors_origins=settings.cors_origins,
            camoufox_available=CAMOUFOX_AVAILABLE,
            uptime_seconds=int(time.time() - startup_time),
        ),
    )


@router.get(
    "/system/info",
    response_model=ApiResponse[SystemInfoData],
    operation_id="get_system_info",
    summary="Get basic API information.",
    description="Basic information about the API.",
)
async def get_system_info():
    """Return basic information about the API."""
    return ApiResponse(
        success=True,
        message="Camoufox Profile Manager API",
        data=SystemInfoData(
            name="camoufox-profile-manager",
            version=__version__,
            description="Antidetect browser profile manager API",
            uptime_seconds=int(time.time() - startup_time),
        ),
    )
