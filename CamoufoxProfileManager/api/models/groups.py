"""
Модели API для работы с группами профилей
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class GroupCreateRequest(BaseModel):
    """Запрос на создание группы"""
    name: str = Field(..., min_length=1, max_length=255, description="Название группы")
    description: Optional[str] = Field(None, max_length=1000, description="Описание группы")
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Социальные сети",
                "description": "Профили для работы с социальными сетями"
            }
        }


class GroupUpdateRequest(BaseModel):
    """Запрос на обновление группы"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Социальные сети (обновлено)",
                "description": "Обновленное описание группы"
            }
        }


class GroupResponse(BaseModel):
    """Ответ с данными группы"""
    id: str
    name: str
    description: Optional[str]
    profile_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
        json_schema_extra = {
            "example": {
                "id": "group_123",
                "name": "Социальные сети",
                "description": "Профили для работы с социальными сетями",
                "profile_count": 5,
                "created_at": "2025-01-17T10:00:00"
            }
        }


class GroupListResponse(BaseModel):
    """Ответ со списком групп"""
    groups: List[GroupResponse]
    total: int
    
    class Config:
        json_schema_extra = {
            "example": {
                "groups": [],
                "total": 3
            }
        } 