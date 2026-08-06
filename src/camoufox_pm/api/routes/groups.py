"""
API роуты для управления группами профилей
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, status
from loguru import logger

from api.models.groups import *
from api.models.system import ApiResponse
from api.dependencies import get_profile_manager


router = APIRouter()


@router.get(
    "/groups",
    response_model=GroupListResponse,
    summary="Получить список групп",
    description="Получить список всех групп профилей"
)
async def list_groups():
    """Получить список всех групп"""
    try:
        profile_manager = get_profile_manager()
        groups_data = await profile_manager.list_groups()
        
        # Преобразуем в API модели
        groups = []
        for group_data in groups_data:
            groups.append(GroupResponse(
                id=group_data['id'],
                name=group_data['name'],
                description=group_data.get('description'),
                profile_count=group_data.get('profile_count', 0),
                created_at=group_data['created_at']
            ))
        
        return GroupListResponse(
            groups=groups,
            total=len(groups)
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения списка групп: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/groups",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новую группу",
    description="Создать новую группу профилей"
)
async def create_group(request: GroupCreateRequest):
    """Создать новую группу"""
    try:
        profile_manager = get_profile_manager()
        
        # Создаем группу
        group_data = await profile_manager.create_group(
            name=request.name,
            description=request.description
        )
        
        logger.success(f"✅ Создана группа: {request.name}")
        
        return GroupResponse(
            id=group_data['id'],
            name=group_data['name'],
            description=group_data.get('description'),
            profile_count=0,
            created_at=group_data['created_at']
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка создания группы: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/groups/{group_id}",
    response_model=GroupResponse,
    summary="Получить группу по ID",
    description="Получить информацию о группе"
)
async def get_group(group_id: str):
    """Получить группу по ID"""
    try:
        profile_manager = get_profile_manager()
        group_data = await profile_manager.get_group(group_id)
        
        if not group_data:
            raise HTTPException(
                status_code=404,
                detail=f"Группа с ID {group_id} не найдена"
            )
        
        return GroupResponse(
            id=group_data['id'],
            name=group_data['name'],
            description=group_data.get('description'),
            profile_count=group_data.get('profile_count', 0),
            created_at=group_data['created_at']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения группы {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/groups/{group_id}",
    response_model=GroupResponse,
    summary="Обновить группу",
    description="Обновить данные группы"
)
async def update_group(group_id: str, request: GroupUpdateRequest):
    """Обновить группу"""
    try:
        profile_manager = get_profile_manager()
        
        # Подготавливаем изменения
        updates = {}
        if request.name is not None:
            updates['name'] = request.name
        if request.description is not None:
            updates['description'] = request.description
        
        # Обновляем группу
        group_data = await profile_manager.update_group(group_id, updates)
        
        if not group_data:
            raise HTTPException(
                status_code=404,
                detail=f"Группа с ID {group_id} не найдена"
            )
        
        logger.info(f"📝 Обновлена группа: {group_data['name']} (ID: {group_id})")
        
        return GroupResponse(
            id=group_data['id'],
            name=group_data['name'],
            description=group_data.get('description'),
            profile_count=group_data.get('profile_count', 0),
            created_at=group_data['created_at']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка обновления группы {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/groups/{group_id}",
    response_model=ApiResponse,
    summary="Удалить группу",
    description="Удалить группу (профили останутся без группы)"
)
async def delete_group(group_id: str):
    """Удалить группу"""
    try:
        profile_manager = get_profile_manager()
        success = await profile_manager.delete_group(group_id)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Группа с ID {group_id} не найдена"
            )
        
        logger.warning(f"🗑️ Удалена группа: ID {group_id}")
        
        return ApiResponse(
            success=True,
            message=f"Группа {group_id} успешно удалена"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления группы {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) 