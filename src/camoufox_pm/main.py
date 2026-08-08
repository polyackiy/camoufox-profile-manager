"""Camoufox Profile Manager REST API."""

from contextlib import asynccontextmanager
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
    set_storage_manager,
)
from camoufox_pm.api.errors import install_error_handlers
from camoufox_pm.api.middleware.logging import LoggingMiddleware
from camoufox_pm.api.models.system import ErrorResponse, HealthResponse
from camoufox_pm.api.routes import auth, groups, profiles, system
from camoufox_pm.config import get_settings
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources on startup and clean up on shutdown."""
    logger.info("Starting Camoufox Profile Manager API...")

    storage_manager = StorageManager(settings.db_path)
    await storage_manager.initialize()

    data_dir = str(Path(settings.db_path).parent)
    profile_manager = ProfileManager(storage_manager, data_dir)
    await profile_manager.initialize()

    set_storage_manager(storage_manager)
    set_profile_manager(profile_manager)

    logger.info("API ready")
    try:
        yield
    finally:
        logger.info("Shutting down API...")
        await storage_manager.close()


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
        system.router,
        prefix=_prefix,
        tags=["System"],
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
            profiles_count=len(profiles_list),
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
