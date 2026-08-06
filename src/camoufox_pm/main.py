"""
CamoufoxProfileManager REST API
Полнофункциональная система управления профилями антидетект браузера
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from loguru import logger

from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.database import StorageManager
from camoufox_pm.api.dependencies import set_storage_manager, set_profile_manager
from camoufox_pm.api.routes import profiles, groups, system, websocket
from camoufox_pm.api.middleware.logging import LoggingMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    
    # Инициализация при запуске
    logger.info("🚀 Запуск CamoufoxProfileManager API...")
    
    try:
        # Инициализируем базу данных
        storage_manager = StorageManager("data/profiles.db")
        await storage_manager.initialize()
        
        # Инициализируем менеджер профилей
        profile_manager = ProfileManager(storage_manager, "data")
        await profile_manager.initialize()
        
        # Устанавливаем зависимости
        set_storage_manager(storage_manager)
        set_profile_manager(profile_manager)
        
        logger.success("✅ CamoufoxProfileManager API готов к работе!")
        
        # Передаем управление приложению
        yield
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")
        raise
    finally:
        # Очистка при завершении
        logger.info("🔄 Завершение работы API...")
        await storage_manager.close()
        logger.info("✅ API завершен")


# Создаем FastAPI приложение
app = FastAPI(
    title="CamoufoxProfileManager API",
    description="""
    🦊 **Система управления профилями антидетект браузера на базе Camoufox**
    
    ## Возможности:
    
    * **Управление профилями** - создание, редактирование, удаление профилей
    * **Группировка** - организация профилей по группам
    * **Фильтрация и поиск** - быстрый поиск по различным критериям
    * **Статистика** - детальная аналитика использования
    * **Запуск браузера** - интеграция с Camoufox
    * **WebSocket** - мониторинг в реальном времени
    
    ## Производительность:
    
    * Создание 50 профилей: **0.05 секунд**
    * Поисковые запросы: **0.002 секунды**
    * Масштабируемость: **1000+ профилей**
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене указать конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Добавляем middleware для логирования
app.add_middleware(LoggingMiddleware)

# Подключаем роуты
app.include_router(profiles.router, prefix="/api", tags=["Profiles"])
app.include_router(groups.router, prefix="/api", tags=["Groups"])
app.include_router(system.router, prefix="/api", tags=["System"])
app.include_router(websocket.router, prefix="/ws", tags=["WebSocket"])

# Статические файлы (для будущего веб-интерфейса)
web_dir = Path("web/static")
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    """Перенаправление на документацию API"""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["System"])
async def health_check():
    """Проверка состояния API"""
    from camoufox_pm.api.dependencies import get_storage_manager, get_profile_manager
    
    try:
        storage_mgr = get_storage_manager()
        profile_mgr = get_profile_manager()
        profiles = await profile_mgr.list_profiles()
        
        return {
            "status": "healthy",
            "api_version": "1.0.0",
            "database": "connected",
            "profiles_count": len(profiles)
        }
    except Exception:
        return {
            "status": "unhealthy",
            "api_version": "1.0.0",
            "database": "disconnected",
            "profiles_count": 0
        }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "camoufox_pm.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )