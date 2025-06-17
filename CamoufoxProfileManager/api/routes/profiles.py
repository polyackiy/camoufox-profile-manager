"""
API роуты для управления профилями
"""

import uuid
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Depends, status
from loguru import logger

from api.models.profiles import *
from api.models.system import ApiResponse, ErrorResponse
from core.models import ProfileStatus
from api.dependencies import get_profile_manager


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
                auto_rotate_fingerprint=profile.auto_rotate_fingerprint,
                rotate_interval_hours=profile.rotate_interval_hours,
                max_sessions_per_day=profile.max_sessions_per_day,
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
            auto_rotate_fingerprint=profile.auto_rotate_fingerprint,
            rotate_interval_hours=profile.rotate_interval_hours,
            max_sessions_per_day=profile.max_sessions_per_day,
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
            auto_rotate_fingerprint=profile.auto_rotate_fingerprint,
            rotate_interval_hours=profile.rotate_interval_hours,
            max_sessions_per_day=profile.max_sessions_per_day,
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
        if request.auto_rotate_fingerprint is not None:
            updates['auto_rotate_fingerprint'] = request.auto_rotate_fingerprint
        if request.rotate_interval_hours is not None:
            updates['rotate_interval_hours'] = request.rotate_interval_hours
        if request.max_sessions_per_day is not None:
            updates['max_sessions_per_day'] = request.max_sessions_per_day
        
        # Обновляем профиль
        profile = await profile_manager.update_profile(profile_id, updates)
        
        if not profile:
            raise HTTPException(
                status_code=404,
                detail=f"Профиль с ID {profile_id} не найден"
            )
        
        logger.info(f"📝 Обновлен профиль: {profile.name} (ID: {profile_id})")
        
        return ProfileResponse(
            id=profile.id,
            name=profile.name,
            group=profile.group,
            status=profile.status,
            browser_settings=profile.browser_settings.dict() if profile.browser_settings else {},
            proxy_config=profile.proxy.dict() if profile.proxy else None,
            storage_path=profile.storage_path,
            notes=profile.notes,
            auto_rotate_fingerprint=profile.auto_rotate_fingerprint,
            rotate_interval_hours=profile.rotate_interval_hours,
            max_sessions_per_day=profile.max_sessions_per_day,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            last_used=profile.last_used
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
            status="launched",
            message="Браузер успешно запущен",
            camoufox_options=browser_session
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
            auto_rotate_fingerprint=cloned_profile.auto_rotate_fingerprint,
            rotate_interval_hours=cloned_profile.rotate_interval_hours,
            max_sessions_per_day=cloned_profile.max_sessions_per_day,
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