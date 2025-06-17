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
    status: str = Field(..., description="Статус системы (healthy/unhealthy)")
    version: str = Field(..., description="Версия API")
    uptime_seconds: int = Field(..., description="Время работы в секундах")
    database: Dict[str, Any] = Field(..., description="Статус базы данных")
    profiles: Dict[str, Any] = Field(..., description="Статистика профилей")
    memory_usage: Dict[str, Any] = Field(..., description="Использование памяти")
    timestamp: datetime = Field(..., description="Время проверки")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "version": "1.0.0",
                "uptime_seconds": 3600,
                "database": {
                    "status": "connected",
                    "size_mb": 0.08,
                    "tables": 3
                },
                "profiles": {
                    "total": 25,
                    "active": 20,
                    "inactive": 5
                },
                "memory_usage": {
                    "rss_mb": 45.2,
                    "vms_mb": 120.5
                },
                "timestamp": "2025-01-17T12:00:00"
            }
        }


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