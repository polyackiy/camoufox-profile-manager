"""Camoufox Profile Manager REST API."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from camoufox_pm import __version__
from camoufox_pm.api.dependencies import (
    require_api_key,
    set_profile_manager,
    set_storage_manager,
)
from camoufox_pm.api.middleware.logging import LoggingMiddleware
from camoufox_pm.api.routes import groups, profiles, system, websocket
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
        "browser, and drive everything over a REST API."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)

# Optional API-key guard; a no-op when CPM_API_KEY is unset.
protected = [Depends(require_api_key)]
app.include_router(profiles.router, prefix="/api", tags=["Profiles"], dependencies=protected)
app.include_router(groups.router, prefix="/api", tags=["Groups"], dependencies=protected)
app.include_router(system.router, prefix="/api", tags=["System"], dependencies=protected)
app.include_router(websocket.router, prefix="/ws", tags=["WebSocket"])

web_dir = Path("web/static")
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    """Redirect to the API documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["System"])
async def health_check():
    """Report API and database health."""
    from camoufox_pm.api.dependencies import get_profile_manager

    try:
        profile_mgr = get_profile_manager()
        profiles_list = await profile_mgr.list_profiles()
        return {
            "status": "healthy",
            "api_version": __version__,
            "database": "connected",
            "profiles_count": len(profiles_list),
        }
    except Exception:
        return {
            "status": "unhealthy",
            "api_version": __version__,
            "database": "disconnected",
            "profiles_count": 0,
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "camoufox_pm.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level="info",
    )
