"""
Модели API для работы с профилями
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator

from core.models import ProfileStatus, BrowserSettings, ProxyConfig


class ProfileCreateRequest(BaseModel):
    """Запрос на создание профиля"""
    name: str = Field(..., min_length=1, max_length=255, description="Название профиля")
    group: Optional[str] = Field(None, description="ID группы профиля")
    browser_settings: Optional[Dict[str, Any]] = Field(
        None, 
        description="Настройки браузера (os, screen, languages, etc.)"
    )
    proxy_config: Optional[Dict[str, Any]] = Field(
        None,
        description="Конфигурация прокси"
    )
    notes: Optional[str] = Field(None, max_length=1000, description="Заметки")
    generate_fingerprint: bool = Field(True, description="Генерировать отпечаток")
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Facebook Профиль 1",
                "group": "social_media_group_id",
                "browser_settings": {
                    "os": "windows",
                    "screen": "1920x1080",
                    "languages": ["ru-RU", "en-US"]
                },
                "proxy_config": {
                    "type": "http",
                    "server": "proxy.example.com:8080",
                    "username": "user",
                    "password": "pass"
                },
                "notes": "Профиль для работы с Facebook",
                "generate_fingerprint": True
            }
        }


class ProfileUpdateRequest(BaseModel):
    """Запрос на обновление профиля"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    group: Optional[str] = Field(None)
    status: Optional[ProfileStatus] = Field(None)
    browser_settings: Optional[Dict[str, Any]] = Field(None)
    proxy_config: Optional[Dict[str, Any]] = Field(None)
    notes: Optional[str] = Field(None, max_length=1000)
    
    # Расширенные настройки браузера
    browser_os: Optional[str] = Field(None, description="Операционная система (windows, macos, linux)")
    browser_screen: Optional[str] = Field(None, description="Разрешение экрана (1920x1080)")
    browser_user_agent: Optional[str] = Field(None, description="User-Agent строка")
    browser_languages: Optional[List[str]] = Field(None, description="Языки браузера")
    browser_timezone: Optional[str] = Field(None, description="Часовой пояс")
    browser_locale: Optional[str] = Field(None, description="Локаль (en_US, ru_RU)")
    browser_webrtc_mode: Optional[str] = Field(None, description="WebRTC режим (forward, replace, real, none)")
    browser_canvas_noise: Optional[bool] = Field(None, description="Canvas шум")
    browser_webgl_noise: Optional[bool] = Field(None, description="WebGL шум")
    browser_audio_noise: Optional[bool] = Field(None, description="Аудио шум")
    browser_hardware_concurrency: Optional[int] = Field(None, ge=1, le=32, description="Количество ядер")
    browser_device_memory: Optional[int] = Field(None, ge=1, le=128, description="Память устройства (GB)")
    browser_max_touch_points: Optional[int] = Field(None, ge=0, le=10, description="Макс. точек касания")
    browser_window_width: Optional[int] = Field(None, ge=800, le=3840, description="Ширина окна браузера")
    browser_window_height: Optional[int] = Field(None, ge=600, le=2160, description="Высота окна браузера")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "inactive",
                "notes": "Обновленные заметки",
                "browser_os": "windows",
                "browser_screen": "1920x1080",
                "browser_webrtc_mode": "replace",
                "browser_canvas_noise": True,
                "browser_webgl_noise": True
            }
        }


class ProfileResponse(BaseModel):
    """Ответ с данными профиля"""
    id: str
    name: str
    group: Optional[str]
    status: ProfileStatus
    browser_settings: Dict[str, Any]
    proxy_config: Optional[Dict[str, Any]]
    storage_path: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_used: Optional[datetime]
    
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


class ProfileListResponse(BaseModel):
    """Ответ со списком профилей"""
    profiles: List[ProfileResponse]
    total: int
    page: int
    per_page: int
    has_next: bool
    has_prev: bool
    
    class Config:
        json_schema_extra = {
            "example": {
                "profiles": [],
                "total": 10,
                "page": 1,
                "per_page": 10,
                "has_next": False,
                "has_prev": False
            }
        }


class ProfileStatsResponse(BaseModel):
    """Ответ со статистикой профиля"""
    profile_id: str
    total_sessions: int
    total_duration_minutes: int
    last_session: Optional[datetime]
    success_rate: float
    actions: List[Dict[str, Any]]
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
        json_schema_extra = {
            "example": {
                "profile_id": "profile_123",
                "total_sessions": 15,
                "total_duration_minutes": 240,
                "last_session": "2025-01-17T12:00:00",
                "success_rate": 0.95,
                "actions": [
                    {
                        "action": "create_profile",
                        "timestamp": "2025-01-17T10:00:00",
                        "success": True
                    }
                ]
            }
        }


class ProfileCloneRequest(BaseModel):
    """Запрос на клонирование профиля"""
    new_name: str = Field(..., min_length=1, max_length=255)
    regenerate_fingerprint: bool = Field(True, description="Генерировать новый отпечаток")
    
    class Config:
        json_schema_extra = {
            "example": {
                "new_name": "Facebook Профиль 1 (копия)",
                "regenerate_fingerprint": True
            }
        }


class ProfileLaunchRequest(BaseModel):
    """Запрос на запуск браузера с профилем"""
    headless: bool = Field(False, description="Запуск в headless режиме")
    window_size: Optional[str] = Field(None, description="Размер окна (1920x1080)")
    additional_options: Optional[Dict[str, Any]] = Field(None, description="Дополнительные опции Camoufox")
    
    class Config:
        json_schema_extra = {
            "example": {
                "headless": False,
                "window_size": "1920x1080",
                "additional_options": {
                    "geoip": True,
                    "humanize": True
                }
            }
        }


class ProfileLaunchResponse(BaseModel):
    """Ответ при запуске браузера"""
    profile_id: str
    browser_session_id: str
    status: str
    message: str
    camoufox_options: Dict[str, Any]
    
    class Config:
        json_schema_extra = {
            "example": {
                "profile_id": "profile_123",
                "browser_session_id": "session_456",
                "status": "launched",
                "message": "Браузер успешно запущен",
                "camoufox_options": {
                    "os": "windows",
                    "screen": "1920x1080",
                    "headless": False
                }
            }
        } 