"""
Системные модели API
"""

from datetime import datetime
from typing import Optional, Dict, Any, List, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar('T')


class ApiResponse(BaseModel, Generic[T]):
    """Базовый ответ API"""
    success: bool = Field(True, description="Статус выполнения операции")
    message: str = Field("", description="Сообщение")
    data: Optional[T] = Field(None, description="Данные ответа")
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "Операция выполнена успешно",
                "data": {}
            }
        }


class ErrorResponse(BaseModel):
    """Ответ с ошибкой"""
    success: bool = Field(False)
    error: str = Field(..., description="Код ошибки")
    message: str = Field(..., description="Описание ошибки")
    details: Optional[Dict[str, Any]] = Field(None, description="Дополнительные детали")
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": False,
                "error": "PROFILE_NOT_FOUND",
                "message": "Профиль не найден",
                "details": {"profile_id": "invalid_id"}
            }
        }


class PaginationResponse(BaseModel):
    """Модель пагинации"""
    page: int = Field(1, ge=1, description="Номер страницы")
    per_page: int = Field(10, ge=1, le=100, description="Элементов на страницу")
    total: int = Field(0, ge=0, description="Общее количество элементов")
    has_next: bool = Field(False, description="Есть следующая страница")
    has_prev: bool = Field(False, description="Есть предыдущая страница")
    
    class Config:
        json_schema_extra = {
            "example": {
                "page": 1,
                "per_page": 10,
                "total": 25,
                "has_next": True,
                "has_prev": False
            }
        }


class SystemStatusResponse(BaseModel):
    """Статус системы"""
    total_profiles: int = Field(..., description="Всего профилей")
    active_profiles: int = Field(..., description="Активных профилей")
    running_browsers: int = Field(..., description="Запущенных браузеров")
    total_groups: int = Field(..., description="Всего групп")
    system_load: float = Field(..., description="Загрузка системы")
    memory_usage: float = Field(..., description="Использование памяти в %")
    disk_usage: float = Field(..., description="Использование диска в %")
    uptime_seconds: int = Field(..., description="Время работы в секундах")
    
    class Config:
        json_schema_extra = {
            "example": {
                "total_profiles": 25,
                "active_profiles": 20,
                "running_browsers": 3,
                "total_groups": 5,
                "system_load": 0.25,
                "memory_usage": 45.2,
                "disk_usage": 67.8,
                "uptime_seconds": 3600
            }
        }


class ProfileDiagnosticResponse(BaseModel):
    """Ответ с результатами диагностики профилей"""
    total_profiles_in_db: int = Field(..., description="Всего профилей в БД")
    total_directories_on_disk: int = Field(..., description="Всего директорий на диске")
    total_disk_size_mb: float = Field(..., description="Общий размер в MB")
    orphaned_directories: int = Field(..., description="Осиротевших директорий")
    orphaned_size_mb: float = Field(..., description="Размер осиротевших директорий в MB")
    missing_directories: int = Field(..., description="Профилей без директорий")
    healthy_profiles: int = Field(..., description="Здоровых профилей")
    issues_found: int = Field(..., description="Найдено проблем")


class ProfileCleanupResponse(BaseModel):
    """Ответ с результатами очистки профилей"""
    orphaned_removed: int = Field(..., description="Удалено осиротевших директорий")
    directories_created: int = Field(..., description="Создано директорий")
    freed_space_mb: float = Field(..., description="Освобождено места в MB")
    dry_run: bool = Field(..., description="Тестовый запуск")
    message: str = Field(..., description="Сообщение о результате")


class WebSocketMessage(BaseModel):
    """Сообщение WebSocket"""
    type: str = Field(..., description="Тип сообщения")
    timestamp: datetime = Field(..., description="Время сообщения")
    data: Dict[str, Any] = Field(..., description="Данные сообщения")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
        json_schema_extra = {
            "example": {
                "type": "profile_created",
                "timestamp": "2025-01-17T12:00:00",
                "data": {
                    "profile_id": "profile_123",
                    "name": "New Profile"
                }
            }
        } 