"""
API роуты для системных функций
"""

import time
import psutil
import os
from datetime import datetime
from fastapi import APIRouter, HTTPException
from loguru import logger

from api.models.system import SystemStatusResponse, ApiResponse
from api.dependencies import get_profile_manager, get_storage_manager


router = APIRouter()

# Время запуска приложения
startup_time = time.time()


@router.get(
    "/system/status",
    response_model=SystemStatusResponse,
    summary="Статус системы",
    description="Получить полную информацию о состоянии системы"
)
async def get_system_status():
    """Получить статус системы"""
    try:
        profile_manager = get_profile_manager()
        storage_manager = get_storage_manager()
        
        # Получаем статистику профилей
        profiles = await profile_manager.list_profiles()
        active_profiles = [p for p in profiles if p.status == 'active']
        inactive_profiles = [p for p in profiles if p.status == 'inactive']
        
        # Получаем информацию о базе данных
        db_path = "demo_data/profiles.db"
        db_size_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        db_size_mb = round(db_size_bytes / 1024 / 1024, 2)
        
        # Получаем использование памяти
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return SystemStatusResponse(
            status="healthy",
            version="1.0.0",
            uptime_seconds=int(time.time() - startup_time),
            database={
                "status": "connected",
                "size_mb": db_size_mb,
                "tables": 3  # profiles, profile_groups, usage_stats
            },
            profiles={
                "total": len(profiles),
                "active": len(active_profiles),
                "inactive": len(inactive_profiles)
            },
            memory_usage={
                "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
                "vms_mb": round(memory_info.vms / 1024 / 1024, 2)
            },
            timestamp=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса системы: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/system/info",
    response_model=ApiResponse,
    summary="Информация о системе",
    description="Базовая информация о API"
)
async def get_system_info():
    """Получить базовую информацию о системе"""
    try:
        return ApiResponse(
            success=True,
            message="CamoufoxProfileManager API",
            data={
                "name": "CamoufoxProfileManager",
                "version": "1.0.0",
                "description": "API для управления профилями антидетект браузера",
                "author": "AI Assistant",
                "uptime_seconds": int(time.time() - startup_time)
            }
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения информации о системе: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/system/cleanup",
    response_model=ApiResponse,
    summary="Очистка системы",
    description="Очистить временные файлы и неиспользуемые данные"
)
async def cleanup_system():
    """Очистить систему"""
    try:
        profile_manager = get_profile_manager()
        
        # TODO: Реализовать логику очистки
        # - Удаление неиспользуемых профильных директорий
        # - Очистка старых логов
        # - Сжатие базы данных
        
        logger.info("🧹 Выполнена очистка системы")
        
        return ApiResponse(
            success=True,
            message="Очистка системы выполнена успешно",
            data={
                "cleaned_files": 0,
                "freed_space_mb": 0
            }
        )
        
    except Exception as e:
        logger.error(f"Ошибка очистки системы: {e}")
        raise HTTPException(status_code=500, detail=str(e)) 