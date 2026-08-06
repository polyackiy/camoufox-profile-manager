"""
API роуты для управления профилями
"""

import uuid
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Depends, status, UploadFile, File
from fastapi.responses import Response
from loguru import logger

from camoufox_pm.api.models.profiles import *
from camoufox_pm.api.models.system import ApiResponse, ErrorResponse
from camoufox_pm.core.models import ProfileStatus
from camoufox_pm.core.excel_manager import ExcelManager
from camoufox_pm.api.dependencies import get_profile_manager


router = APIRouter()


@router.get(
    "/profiles",
    response_model=ProfileListResponse,
    summary="Получить список профилей",
    description="Получить список всех профилей с поддержкой фильтрации и пагинации"
)
async def list_profiles(
    page: int = Query(1, ge=1, description="Номер страницы"),
    per_page: int = Query(10, ge=1, le=100, description="Профилей на страницу"),
    status: Optional[ProfileStatus] = Query(None, description="Фильтр по статусу"),
    group: Optional[str] = Query(None, description="Фильтр по группе"),
    search: Optional[str] = Query(None, description="Поиск по названию")
):
    """Получить список профилей с фильтрацией"""
    try:
        profile_manager = get_profile_manager()
        
        # Подготавливаем фильтры
        filters = {}
        if status:
            filters['status'] = status
        if group:
            filters['group'] = group
        if search:
            filters['name_contains'] = search
            
        # Получаем профили
        profiles = await profile_manager.list_profiles(filters=filters)
        
        # Пагинация
        total = len(profiles)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_profiles = profiles[start_idx:end_idx]
        
        # Преобразуем в API модели
        profile_responses = []
        for profile in paginated_profiles:
            profile_responses.append(ProfileResponse(
                id=profile.id,
                name=profile.name,
                group=profile.group,
                status=profile.status,
                browser_settings=profile.browser_settings.dict() if profile.browser_settings else {},
                proxy_config=profile.proxy.dict() if profile.proxy else None,
                storage_path=profile.storage_path,
                notes=profile.notes,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
                last_used=profile.last_used
            ))
        
        return ProfileListResponse(
            profiles=profile_responses,
            total=total,
            page=page,
            per_page=per_page,
            has_next=end_idx < total,
            has_prev=page > 1
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения списка профилей: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/profiles",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новый профиль",
    description="Создать новый профиль с автоматической генерацией отпечатка"
)
async def create_profile(request: ProfileCreateRequest):
    """Создать новый профиль"""
    try:
        profile_manager = get_profile_manager()
        
        # Создаем профиль с правильными параметрами
        profile = await profile_manager.create_profile(
            name=request.name,
            group=request.group,
            browser_settings=request.browser_settings,
            proxy_config=request.proxy_config,
            generate_fingerprint=request.generate_fingerprint
        )
        
        logger.success(f"✅ Создан профиль: {profile.name} (ID: {profile.id})")
        
        return ProfileResponse(
            id=profile.id,
            name=profile.name,
            group=profile.group,
            status=profile.status,
            browser_settings=profile.browser_settings.dict() if profile.browser_settings else {},
            proxy_config=profile.proxy.dict() if profile.proxy else None,
            storage_path=profile.storage_path,
            notes=profile.notes,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            last_used=profile.last_used
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка создания профиля: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/profiles/{profile_id}",
    response_model=ProfileResponse,
    summary="Получить профиль по ID",
    description="Получить детальную информацию о профиле"
)
async def get_profile(profile_id: str):
    """Получить профиль по ID"""
    try:
        profile_manager = get_profile_manager()
        profile = await profile_manager.get_profile(profile_id)
        
        if not profile:
            raise HTTPException(
                status_code=404,
                detail=f"Профиль с ID {profile_id} не найден"
            )
        
        return ProfileResponse(
            id=profile.id,
            name=profile.name,
            group=profile.group,
            status=profile.status,
            browser_settings=profile.browser_settings.dict() if profile.browser_settings else {},
            proxy_config=profile.proxy.dict() if profile.proxy else None,
            storage_path=profile.storage_path,
            notes=profile.notes,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            last_used=profile.last_used
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/profiles/{profile_id}",
    response_model=ProfileResponse,
    summary="Обновить профиль",
    description="Обновить данные профиля"
)
async def update_profile(profile_id: str, request: ProfileUpdateRequest):
    """Обновить профиль"""
    try:
        profile_manager = get_profile_manager()
        
        # Подготавливаем изменения
        updates = {}
        if request.name is not None:
            updates['name'] = request.name
        if request.group is not None:
            updates['group'] = request.group
        if request.status is not None:
            updates['status'] = request.status
        if request.browser_settings is not None:
            updates['browser_settings'] = request.browser_settings
        if request.proxy_config is not None:
            updates['proxy_config'] = request.proxy_config
        if request.notes is not None:
            updates['notes'] = request.notes
            
        # Обработка расширенных настроек браузера
        browser_updates = {}
        if request.browser_os is not None:
            browser_updates['os'] = request.browser_os
        if request.browser_screen is not None:
            browser_updates['screen'] = request.browser_screen
        if request.browser_user_agent is not None:
            browser_updates['user_agent'] = request.browser_user_agent
        if request.browser_languages is not None:
            browser_updates['languages'] = request.browser_languages
        if request.browser_timezone is not None:
            browser_updates['timezone'] = request.browser_timezone
        if request.browser_locale is not None:
            browser_updates['locale'] = request.browser_locale
        if request.browser_webrtc_mode is not None:
            browser_updates['webrtc_mode'] = request.browser_webrtc_mode
        if request.browser_canvas_noise is not None:
            browser_updates['canvas_noise'] = request.browser_canvas_noise
        if request.browser_webgl_noise is not None:
            browser_updates['webgl_noise'] = request.browser_webgl_noise
        if request.browser_audio_noise is not None:
            browser_updates['audio_noise'] = request.browser_audio_noise
        if request.browser_hardware_concurrency is not None:
            browser_updates['hardware_concurrency'] = request.browser_hardware_concurrency
        if request.browser_device_memory is not None:
            browser_updates['device_memory'] = request.browser_device_memory
        if request.browser_max_touch_points is not None:
            browser_updates['max_touch_points'] = request.browser_max_touch_points
        if request.browser_window_width is not None:
            browser_updates['window_width'] = request.browser_window_width
        if request.browser_window_height is not None:
            browser_updates['window_height'] = request.browser_window_height
            
        # Если есть обновления настроек браузера, добавляем их
        if browser_updates:
            # Получаем текущий профиль для обновления настроек браузера
            current_profile = await profile_manager.get_profile(profile_id)
            if current_profile and current_profile.browser_settings:
                current_settings = current_profile.browser_settings.dict()
                current_settings.update(browser_updates)
                updates['browser_settings'] = current_settings
        
        # Обновляем профиль
        updated_profile = await profile_manager.update_profile(profile_id, updates)
        
        if not updated_profile:
            raise HTTPException(
                status_code=404,
                detail=f"Профиль с ID {profile_id} не найден"
            )
        
        logger.success(f"✅ Обновлен профиль: {updated_profile.name} (ID: {profile_id})")
        
        return ProfileResponse(
            id=updated_profile.id,
            name=updated_profile.name,
            group=updated_profile.group,
            status=updated_profile.status,
            browser_settings=updated_profile.browser_settings.dict() if updated_profile.browser_settings else {},
            proxy_config=updated_profile.proxy.dict() if updated_profile.proxy else None,
            storage_path=updated_profile.storage_path,
            notes=updated_profile.notes,
            created_at=updated_profile.created_at,
            updated_at=updated_profile.updated_at,
            last_used=updated_profile.last_used
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка обновления профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/profiles/{profile_id}",
    response_model=ApiResponse,
    summary="Удалить профиль",
    description="Удалить профиль и все связанные данные"
)
async def delete_profile(profile_id: str):
    """Удалить профиль"""
    try:
        profile_manager = get_profile_manager()
        success = await profile_manager.delete_profile(profile_id)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Профиль с ID {profile_id} не найден"
            )
        
        logger.warning(f"🗑️ Удален профиль: ID {profile_id}")
        
        return ApiResponse(
            success=True,
            message=f"Профиль {profile_id} успешно удален"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/profiles/{profile_id}/launch",
    response_model=ProfileLaunchResponse,
    summary="Запустить браузер с профилем",
    description="Запустить Camoufox с настройками профиля"
)
async def launch_profile(profile_id: str, request: ProfileLaunchRequest):
    """Запустить браузер с профилем"""
    try:
        profile_manager = get_profile_manager()
        
        # Запускаем браузер
        browser_session = await profile_manager.launch_browser(
            profile_id, 
            headless=request.headless,
            window_size=request.window_size,
            **request.additional_options or {}
        )
        
        logger.success(f"🚀 Запущен браузер для профиля: {profile_id}")
        
        return ProfileLaunchResponse(
            profile_id=profile_id,
            browser_session_id=str(uuid.uuid4()),  # Заглушка для ID сессии
            status=browser_session.get("status", "launched"),
            message=browser_session.get("message", "Браузер успешно запущен"),
            camoufox_options={
                "process_id": browser_session.get("process_id"),
                "status": browser_session.get("status"),
                "options": browser_session.get("camoufox_options", {})
            }
        )
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка запуска браузера для профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/profiles/{profile_id}/clone",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Клонировать профиль",
    description="Создать копию профиля с новым отпечатком"
)
async def clone_profile(profile_id: str, request: ProfileCloneRequest):
    """Клонировать профиль"""
    try:
        profile_manager = get_profile_manager()
        
        # Клонируем профиль
        cloned_profile = await profile_manager.clone_profile(
            profile_id,
            request.new_name,
            regenerate_fingerprint=request.regenerate_fingerprint
        )
        
        logger.success(f"🔄 Клонирован профиль: {profile_id} → {cloned_profile.id}")
        
        return ProfileResponse(
            id=cloned_profile.id,
            name=cloned_profile.name,
            group=cloned_profile.group,
            status=cloned_profile.status,
            browser_settings=cloned_profile.browser_settings.dict() if cloned_profile.browser_settings else {},
            proxy_config=cloned_profile.proxy.dict() if cloned_profile.proxy else None,
            storage_path=cloned_profile.storage_path,
            notes=cloned_profile.notes,
            created_at=cloned_profile.created_at,
            updated_at=cloned_profile.updated_at,
            last_used=cloned_profile.last_used
        )
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка клонирования профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/profiles/{profile_id}/stats",
    response_model=ProfileStatsResponse,
    summary="Получить статистику профиля",
    description="Получить статистику использования профиля"
)
async def get_profile_stats(profile_id: str):
    """Получить статистику профиля"""
    try:
        profile_manager = get_profile_manager()
        
        # Проверяем существование профиля
        profile = await profile_manager.get_profile(profile_id)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail=f"Профиль с ID {profile_id} не найден"
            )
        
        # Получаем статистику
        stats = await profile_manager.get_profile_stats(profile_id)
        
        return ProfileStatsResponse(
            profile_id=profile_id,
            total_sessions=stats.get('total_sessions', 0),
            total_duration_minutes=stats.get('total_duration_minutes', 0),
            last_session=stats.get('last_session'),
            success_rate=stats.get('success_rate', 0.0),
            actions=stats.get('actions', [])
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения статистики профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/profiles/{profile_id}/reset-fingerprint",
    response_model=ProfileResponse,
    summary="Сбросить отпечаток профиля",
    description="Полностью регенерировать отпечаток браузера для профиля"
)
async def reset_profile_fingerprint(profile_id: str):
    """Сбросить и регенерировать отпечаток профиля"""
    try:
        profile_manager = get_profile_manager()
        
        # Проверяем существование профиля
        profile = await profile_manager.get_profile(profile_id)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail=f"Профиль с ID {profile_id} не найден"
            )
        
        # Регенерируем отпечаток
        updated_profile = await profile_manager.rotate_profile_fingerprint(profile_id)
        
        logger.success(f"✅ Сброшен отпечаток профиля: {updated_profile.name} (ID: {profile_id})")
        
        return ProfileResponse(
            id=updated_profile.id,
            name=updated_profile.name,
            group=updated_profile.group,
            status=updated_profile.status,
            browser_settings=updated_profile.browser_settings.dict() if updated_profile.browser_settings else {},
            proxy_config=updated_profile.proxy.dict() if updated_profile.proxy else None,
            storage_path=updated_profile.storage_path,
            notes=updated_profile.notes,
            created_at=updated_profile.created_at,
            updated_at=updated_profile.updated_at,
            last_used=updated_profile.last_used
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка сброса отпечатка профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/profiles/export/excel",
    summary="Экспорт профилей в Excel",
    description="Экспортировать все профили в Excel файл для массового редактирования"
)
async def export_profiles_to_excel():
    """Экспорт всех профилей в Excel файл"""
    try:
        profile_manager = get_profile_manager()
        excel_manager = ExcelManager(profile_manager)
        
        # Экспортируем профили
        excel_data = await excel_manager.export_profiles_to_excel()
        
        logger.success("📊 Профили экспортированы в Excel")
        
        return Response(
            content=excel_data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=camoufox_profiles.xlsx"
            }
        )
        
    except Exception as e:
        logger.error(f"Ошибка экспорта профилей в Excel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/profiles/import/excel",
    response_model=ApiResponse,
    summary="Импорт профилей из Excel",
    description="Импортировать профили из Excel файла с автоматическим созданием/обновлением"
)
async def import_profiles_from_excel(file: UploadFile = File(...)):
    """Импорт профилей из Excel файла"""
    try:
        # Проверяем тип файла
        if not file.filename.endswith(('.xlsx', '.xls')):
            raise HTTPException(
                status_code=400,
                detail="Поддерживаются только файлы Excel (.xlsx, .xls)"
            )
        
        # Читаем файл
        excel_data = await file.read()
        
        profile_manager = get_profile_manager()
        excel_manager = ExcelManager(profile_manager)
        
        # Импортируем профили
        result = await excel_manager.import_profiles_from_excel(excel_data)
        
        if result["success"]:
            logger.success(f"📥 Импорт завершен: создано {result['created_count']}, обновлено {result['updated_count']}")
        else:
            logger.warning(f"📥 Импорт с ошибками: {result['error_count']} ошибок")
        
        return ApiResponse(
            success=result["success"],
            message=result["summary"],
            data={
                "created_count": result["created_count"],
                "updated_count": result["updated_count"],
                "error_count": result["error_count"],
                "errors": result["errors"]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка импорта профилей из Excel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/profiles/{profile_id}/close",
    summary="Закрыть браузер профиля",
    description="Принудительно закрыть браузер для указанного профиля"
)
async def close_profile_browser(profile_id: str):
    """Закрыть браузер для профиля"""
    try:
        profile_manager = get_profile_manager()
        result = await profile_manager.close_browser(profile_id)
        
        logger.success(f"🔒 Браузер закрыт для профиля: {profile_id}")
        return result
        
    except Exception as e:
        logger.error(f"Ошибка закрытия браузера для профиля {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/browsers/active",
    summary="Получить активные браузеры",
    description="Получить список всех активных браузеров"
)
async def get_active_browsers():
    """Получить список активных браузеров"""
    try:
        profile_manager = get_profile_manager()
        active_browsers = await profile_manager.get_active_browsers()
        
        return {
            "active_browsers": active_browsers,
            "count": len(active_browsers)
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения активных браузеров: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/browsers/close-all",
    summary="Закрыть все браузеры",
    description="Принудительно закрыть все активные браузеры"
)
async def close_all_browsers():
    """Закрыть все активные браузеры"""
    try:
        profile_manager = get_profile_manager()
        result = await profile_manager.close_all_browsers()
        
        logger.success(f"🔒 Закрыто браузеров: {result['closed_count']}")
        return result
        
    except Exception as e:
        logger.error(f"Ошибка закрытия всех браузеров: {e}")
        raise HTTPException(status_code=500, detail=str(e)) 