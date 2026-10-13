"""Camoufox Profile Manager REST API."""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from camoufox_pm import __version__
from camoufox_pm.api.dependencies import (
    require_auth,
    set_profile_manager,
    set_scheduler,
    set_storage_manager,
)
from camoufox_pm.api.errors import install_error_handlers
from camoufox_pm.api.middleware.access import AccessMiddleware
from camoufox_pm.api.middleware.logging import LoggingMiddleware
from camoufox_pm.api.middleware.maintenance import MaintenanceMiddleware
from camoufox_pm.api.models.system import ErrorResponse, HealthResponse
from camoufox_pm.api.routes import (
    access,
    auth,
    cookies,
    extensions,
    groups,
    profiles,
    proxies,
    recovery,
    runtime,
    saved_filters,
    schedules,
    sync,
    system,
    templates,
)
from camoufox_pm.config import get_settings
from camoufox_pm.core.backups import ProfileBackupManager
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.maintenance import MaintenanceBlocked, MaintenanceGate
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.scheduler import TaskScheduler

settings = get_settings()
maintenance_gate = MaintenanceGate(
    Path(settings.db_path).parent / "update-control" / "maintenance.sqlite3"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources on startup and clean up on shutdown."""
    logger.info("Starting Camoufox Profile Manager API...")

    if maintenance_gate.blocked:
        raise MaintenanceBlocked(
            "An interrupted update requires recovery before opening application data"
        )
    storage_manager = StorageManager(settings.db_path)
    profile_manager = None
    scheduler = None
    backup_task = None
    sync_task = None
    try:
        with maintenance_gate.operation():
            maintenance_gate.register_manager()
            await storage_manager.initialize()
            data_dir = str(Path(settings.db_path).parent)
            profile_manager = ProfileManager(storage_manager, data_dir)
            await profile_manager.initialize()
            set_storage_manager(storage_manager)
            set_profile_manager(profile_manager)
            sync.set_sync_directory(
                Path(settings.db_path).expanduser().resolve().parent / "sync-control"
            )

            def sync_runtime():
                from camoufox_pm.sync.runtime import SyncRuntime

                return SyncRuntime(profile_manager, maintenance_gate)

            sync.set_sync_runtime_factory(sync_runtime)

            backup_manager = ProfileBackupManager(
                profile_manager,
                retention=settings.backup_retention,
                interval_hours=settings.backup_interval_hours,
            )
            recovery.set_backup_manager(backup_manager)

            async def back_up_idle_profiles():
                while True:
                    try:
                        with maintenance_gate.operation():
                            await backup_manager.create_due_backups()
                    except Exception as exc:
                        logger.warning(f"Automatic profile backup failed: {exc}")
                    await asyncio.sleep(60)

            scheduler = TaskScheduler(storage_manager, profile_manager)
            scheduler.maintenance_gate = maintenance_gate
            await scheduler.start()
            set_scheduler(scheduler)
            backup_task = asyncio.create_task(back_up_idle_profiles(), name="profile-backups")
            from camoufox_pm.core.sync_background import sync_queue_worker

            sync_task = asyncio.create_task(
                sync_queue_worker(profile_manager, maintenance_gate), name="profile-sync"
            )
        logger.info("API ready")
        yield
    finally:
        # This also runs when startup fails, so a failed desktop launch leaves
        # neither a heartbeat nor a recovery task holding the database open.
        logger.info("Shutting down API...")
        try:
            if sync_task is not None:
                sync_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sync_task
            if backup_task is not None:
                backup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await backup_task
            if scheduler is not None:
                await scheduler.stop()
            if profile_manager is not None:
                # Stop renewal before releasing any of this instance's leases.
                await profile_manager.browser_sessions.stop_heartbeat()
                try:
                    await profile_manager.browser_sessions.close_all()
                except Exception as exc:
                    logger.warning(f"Failed to close browsers on shutdown: {exc}")
                await profile_manager.release_all_leases()
        finally:
            sync.set_sync_runtime_factory(None)
            sync.set_sync_directory(None)
            recovery.set_backup_manager(None)
            await storage_manager.close()
            maintenance_gate.unregister_manager()


app = FastAPI(
    title="Camoufox Profile Manager API",
    description=(
        "Self-hosted, open-source antidetect browser profile manager built on "
        "Camoufox. Manage profiles and groups, generate fingerprints, launch the "
        "browser, and drive everything over a REST API.\n\n"
        "Endpoints live under `/api/v1`. The unversioned `/api/...` paths from "
        "before 0.2 keep working as aliases but are left out of this schema; "
        "see the stability contract in `docs/api.md`."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    responses={"4XX": {"model": ErrorResponse}, "5XX": {"model": ErrorResponse}},
)
install_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Auth is via the X-API-Key header, not cookies; credentialed CORS would also
    # be rejected by browsers when combined with the wildcard method/header lists.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)
app.add_middleware(AccessMiddleware)
app.add_middleware(MaintenanceMiddleware, gate=maintenance_gate)

# Session-or-API-key guard; a no-op when neither users nor CPM_API_KEY exist.
protected = [Depends(require_auth)]
# /api/v1 is the canonical prefix. The same routers are also served under the
# pre-0.2 unversioned /api so existing scripts keep working; those copies are
# left out of the schema so a generated client sees each operation once.
for _prefix, _in_schema in (("/api/v1", True), ("/api", False)):
    # Unguarded on purpose: login has to work logged out, and /auth/session is
    # how the UI decides whether to show the login screen.
    app.include_router(
        auth.router,
        prefix=_prefix,
        tags=["Auth"],
        include_in_schema=_in_schema,
    )
    app.include_router(
        sync.router,
        prefix=_prefix,
        tags=["Sync"],
        dependencies=protected,
        include_in_schema=_in_schema,
    )
    app.include_router(
        access.router,
        prefix=_prefix,
        tags=["Access"],
        dependencies=protected,
        include_in_schema=_in_schema,
    )
    app.include_router(
        profiles.router,
        prefix=_prefix,
        tags=["Profiles"],
        dependencies=protected,
        include_in_schema=_in_schema,
    )
    app.include_router(
        groups.router,
        prefix=_prefix,
        tags=["Groups"],
        dependencies=protected,
        include_in_schema=_in_schema,
    )
    app.include_router(
        schedules.router,
        prefix=_prefix,
        tags=["Schedules"],
        dependencies=protected,
        include_in_schema=_in_schema,
    )
    app.include_router(
        system.router,
        prefix=_prefix,
        tags=["System"],
        dependencies=protected,
        include_in_schema=_in_schema,
    )
    for _router in (recovery.router, runtime.router, proxies.router):
        app.include_router(
            _router,
            prefix=_prefix,
            tags=["System"],
            dependencies=protected,
            include_in_schema=_in_schema,
        )
    for _router, _tag in (
        (cookies.router, "Cookies"),
        (extensions.router, "Extensions"),
        (templates.router, "Templates"),
        (saved_filters.router, "Saved views"),
    ):
        app.include_router(
            _router,
            prefix=_prefix,
            tags=[_tag],
            dependencies=protected,
            include_in_schema=_in_schema,
        )


@app.get(
    "/health",
    tags=["System"],
    response_model=HealthResponse,
    operation_id="health_check",
    summary="Report API and database health.",
)
async def health_check():
    """Report API and database health. Unversioned so probes survive API versions."""
    from camoufox_pm.api.dependencies import get_profile_manager

    try:
        profile_mgr = get_profile_manager()
        profiles_list = await profile_mgr.list_profiles()
        return HealthResponse(
            status="healthy",
            api_version=__version__,
            database="connected",
            profiles_count=None
            if await profile_mgr.storage.count_users() or get_settings().api_key
            else len(profiles_list),
        )
    except Exception:
        # 503 rather than a healthy-looking 200, so load balancers and container
        # healthchecks see the failure without parsing the body.
        return JSONResponse(
            status_code=503,
            content=HealthResponse(
                status="unhealthy",
                api_version=__version__,
                database="disconnected",
                profiles_count=0,
            ).model_dump(),
        )


def _webui_dir() -> Path | None:
    """Locate the bundled static web UI, if present.

    Looks at ``CPM_WEBUI_DIR``, then the package's ``webui/`` directory (populated
    by ``scripts/build_webui.py``), then ``web/out`` for running from source.
    """
    candidates = [
        settings.webui_dir,
        str(Path(__file__).parent / "webui"),
        "web/out",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


# Serve the web UI on the same origin as the API when a build is available;
# otherwise redirect the root to the API docs. Mounted last so /api and /health
# and /docs take precedence over the catch-all static mount.
_webui = _webui_dir()
if _webui is not None:
    app.mount("/", StaticFiles(directory=str(_webui), html=True), name="webui")
    logger.info(f"Serving web UI from {_webui}")
else:

    @app.get("/", include_in_schema=False)
    async def root():
        """Redirect to the API documentation when no web UI is bundled."""
        return RedirectResponse(url="/docs")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "camoufox_pm.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level="info",
    )
