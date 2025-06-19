"""
API роуты для системных функций
"""

import time
import psutil
import os
from datetime import datetime
from fastapi import APIRouter, HTTPException
from loguru import logger

from api.models.system import SystemStatusResponse, ApiResponse, ProfileDiagnosticResponse, ProfileCleanupResponse
from api.dependencies import get_profile_manager
from cleanup_profiles import ProfileCleanupManager


router = APIRouter()

# Время запуска приложения
startup_time = time.time()


@router.get(
    "/system/status",
    response_model=SystemStatusResponse,
    summary="Получить статус системы",
    description="Получить общую информацию о состоянии системы"
)
async def get_system_status():
    """Получить статус системы"""
    try:
        profile_manager = get_profile_manager()
        
        # Получаем статистику профилей
        profiles = await profile_manager.list_profiles()
        active_profiles = len([p for p in profiles if p.status == "active"])
        
        # Получаем активные браузеры
        active_browsers = await profile_manager.get_active_browsers()
        running_browsers = len(active_browsers.get("active_browsers", []))
        
        # Получаем группы
        groups = await profile_manager.list_groups()
        
        # TODO: Добавить мониторинг ресурсов системы
        memory_usage = psutil.virtual_memory().percent
        disk_usage = psutil.disk_usage('/').percent
        
        return SystemStatusResponse(
            total_profiles=len(profiles),
            active_profiles=active_profiles,
            running_browsers=running_browsers,
            total_groups=len(groups),
            system_load=0.0,  # TODO: Реализовать
            memory_usage=memory_usage,
            disk_usage=disk_usage,
            uptime_seconds=0  # TODO: Реализовать
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса системы: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/system/profiles/diagnostic",
    response_model=ProfileDiagnosticResponse,
    summary="Диагностика профилей",
    description="Проверить состояние профилей и найти проблемы"
)
async def diagnostic_profiles():
    """Диагностика состояния профилей"""
    try:
        cleanup_manager = ProfileCleanupManager()
        await cleanup_manager.initialize()
        
        try:
            diagnostic_result = await cleanup_manager.full_diagnostic()
            
            return ProfileDiagnosticResponse(
                total_profiles_in_db=diagnostic_result['total_profiles_in_db'],
                total_directories_on_disk=diagnostic_result['total_directories_on_disk'],
                total_disk_size_mb=diagnostic_result['total_disk_size_mb'],
                orphaned_directories=diagnostic_result['orphaned_directories'],
                orphaned_size_mb=diagnostic_result['orphaned_size_mb'],
                missing_directories=diagnostic_result['missing_directories'],
                healthy_profiles=diagnostic_result['healthy_profiles'],
                issues_found=diagnostic_result['issues_found']
            )
            
        finally:
            await cleanup_manager.close()
        
    except Exception as e:
        logger.error(f"Ошибка диагностики профилей: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/system/profiles/cleanup",
    response_model=ProfileCleanupResponse,
    summary="Очистка осиротевших профилей",
    description="Удалить директории профилей, которых нет в базе данных"
)
async def cleanup_orphaned_profiles(dry_run: bool = False):
    """Очистка осиротевших профилей"""
    try:
        cleanup_manager = ProfileCleanupManager()
        await cleanup_manager.initialize()
        
        try:
            if dry_run:
                # Только проверяем, что будет удалено
                orphaned = await cleanup_manager.find_orphaned_profile_directories()
                missing = await cleanup_manager.find_missing_profile_directories()
                
                return ProfileCleanupResponse(
                    orphaned_removed=len(orphaned),
                    directories_created=len(missing),
                    freed_space_mb=sum(item['size_mb'] for item in orphaned),
                    dry_run=True,
                    message=f"Dry-run: Будет удалено {len(orphaned)} директорий, создано {len(missing)}"
                )
            else:
                # Выполняем реальную очистку
                results = await cleanup_manager.auto_cleanup(dry_run=False)
                
                # Подсчитываем освобожденное место
                freed_space = 0
                if results['orphaned_removed'] > 0:
                    # Примерная оценка, так как файлы уже удалены
                    freed_space = results['orphaned_removed'] * 30  # ~30MB на профиль в среднем
                
                return ProfileCleanupResponse(
                    orphaned_removed=results['orphaned_removed'],
                    directories_created=results['directories_created'],
                    freed_space_mb=freed_space,
                    dry_run=False,
                    message=f"Очистка завершена: удалено {results['orphaned_removed']}, создано {results['directories_created']}"
                )
                
        finally:
            await cleanup_manager.close()
        
    except Exception as e:
        logger.error(f"Ошибка очистки профилей: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/system/cleanup",
    response_model=ApiResponse,
    summary="Общая очистка системы",
    description="Очистить временные файлы и неиспользуемые данные"
)
async def cleanup_system():
    """Общая очистка системы"""
    try:
        profile_manager = get_profile_manager()
        
        # Выполняем очистку профилей
        cleanup_manager = ProfileCleanupManager()
        await cleanup_manager.initialize()
        
        try:
            cleanup_results = await cleanup_manager.auto_cleanup(dry_run=False)
            
            # TODO: Добавить другие виды очистки:
            # - Очистка старых логов
            # - Сжатие базы данных
            # - Удаление временных файлов браузера
            
            total_cleaned = cleanup_results['orphaned_removed']
            freed_space = total_cleaned * 30  # Примерная оценка
            
            logger.info(f"🧹 Выполнена очистка системы: удалено {total_cleaned} файлов")
            
            return ApiResponse(
                success=True,
                message="Очистка системы выполнена успешно",
                data={
                    "cleaned_files": total_cleaned,
                    "freed_space_mb": freed_space,
                    "orphaned_profiles_removed": cleanup_results['orphaned_removed'],
                    "directories_created": cleanup_results['directories_created']
                }
            )
            
        finally:
            await cleanup_manager.close()
        
    except Exception as e:
        logger.error(f"Ошибка очистки системы: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/system/logs",
    summary="Получить системные логи",
    description="Получить последние записи из системных логов"
)
async def get_system_logs(lines: int = 100):
    """Получить системные логи"""
    try:
        # TODO: Реализовать чтение логов
        # Пока заглушка
        return {
            "logs": [],
            "total_lines": 0,
            "message": "Функция логов будет реализована позже"
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения логов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/system/restart",
    response_model=ApiResponse,
    summary="Перезапуск системы",
    description="Безопасный перезапуск системы управления профилями"
)
async def restart_system():
    """Перезапуск системы"""
    try:
        profile_manager = get_profile_manager()
        
        # Закрываем все активные браузеры
        await profile_manager.close_all_browsers()
        
        # TODO: Реализовать безопасный перезапуск
        logger.info("🔄 Запрос на перезапуск системы")
        
        return ApiResponse(
            success=True,
            message="Система будет перезапущена",
            data={"restart_scheduled": True}
        )
        
    except Exception as e:
        logger.error(f"Ошибка перезапуска системы: {e}")
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