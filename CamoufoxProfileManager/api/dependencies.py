"""
Зависимости для API (Dependency Injection)
"""

from typing import Optional
from fastapi import HTTPException

from core.profile_manager import ProfileManager
from core.database import StorageManager

# Глобальные переменные для хранения экземпляров
_storage_manager: Optional[StorageManager] = None
_profile_manager: Optional[ProfileManager] = None


def set_storage_manager(storage_manager: StorageManager):
    """Установить экземпляр StorageManager"""
    global _storage_manager
    _storage_manager = storage_manager


def set_profile_manager(profile_manager: ProfileManager):
    """Установить экземпляр ProfileManager"""
    global _profile_manager
    _profile_manager = profile_manager


def get_storage_manager() -> StorageManager:
    """Получить экземпляр StorageManager"""
    if _storage_manager is None:
        raise HTTPException(status_code=500, detail="StorageManager не инициализирован")
    return _storage_manager


def get_profile_manager() -> ProfileManager:
    """Получить экземпляр ProfileManager"""  
    if _profile_manager is None:
        raise HTTPException(status_code=500, detail="ProfileManager не инициализирован")
    return _profile_manager 