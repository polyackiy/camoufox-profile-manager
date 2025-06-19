"""
Модели данных для API миграции Chrome
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime


class ChromeProfileDiscoveryRequest(BaseModel):
    """Запрос на обнаружение профилей Chrome"""
    chrome_data_path: Optional[str] = None


class ChromeProfileInfo(BaseModel):
    """Информация о профиле Chrome"""
    name: str
    display_name: str
    path: str
    cookies_path: Optional[str]
    preferences_path: Optional[str]
    has_cookies: bool
    migration_status: str = "not_migrated"
    suggested_mapping: Optional[Dict[str, Any]] = None


class ChromeProfileDiscoveryResponse(BaseModel):
    """Ответ обнаружения профилей Chrome"""
    success: bool
    profiles_found: int
    chrome_profiles: List[ChromeProfileInfo]
    message: str


class MigrationRequest(BaseModel):
    """Запрос на миграцию профиля"""
    chrome_profile_name: str
    chrome_data_path: Optional[str] = None
    camoufox_profile_id: Optional[str] = None  # Если указан, мигрируем в существующий
    new_profile_name: Optional[str] = None     # Если создаем новый
    new_profile_group: Optional[str] = None
    include_cookies: bool = True
    include_bookmarks: bool = True
    include_history: bool = False


class MigrationResponse(BaseModel):
    """Ответ миграции профиля"""
    success: bool
    chrome_profile_name: str
    chrome_display_name: str
    camoufox_profile_id: Optional[str]
    camoufox_profile_name: Optional[str]
    cookies_imported: int = 0
    bookmarks_imported: int = 0
    history_imported: int = 0
    errors: List[str] = []
    message: str


class ProfileMapping(BaseModel):
    """Маппинг профиля для массовой миграции"""
    chrome_profile: Optional[str] = None
    chrome_display_name: Optional[str] = None
    chrome_profile_pattern: Optional[str] = None
    camoufox_profile_id: Optional[str] = None
    create_new_profile: bool = False
    new_profile_name: Optional[str] = None
    new_profile_group: Optional[str] = None
    migration_settings: Dict[str, bool] = {
        "include_cookies": True,
        "include_bookmarks": True,
        "include_history": False
    }


class BulkMigrationRequest(BaseModel):
    """Запрос на массовую миграцию профилей"""
    chrome_data_path: Optional[str] = None
    dry_run: bool = False
    custom_mapping: Optional[List[ProfileMapping]] = None


class MigrationResult(BaseModel):
    """Результат миграции одного профиля"""
    chrome_profile: str
    chrome_display_name: str
    success: bool
    camoufox_profile_id: Optional[str]
    camoufox_profile_name: Optional[str]
    migration_details: Dict[str, Any] = {}
    errors: List[str] = []
    started_at: str
    completed_at: Optional[str] = None


class BulkMigrationResponse(BaseModel):
    """Ответ массовой миграции"""
    success: bool
    dry_run: bool
    chrome_profiles_found: int
    profiles_migrated: int
    profiles_failed: int
    migration_results: List[MigrationResult]
    errors: List[str] = []
    started_at: str
    completed_at: Optional[str] = None
    message: str


class MigratedProfileDetail(BaseModel):
    """Детали мигрированного профиля"""
    id: str
    name: str
    group: Optional[str]
    created_at: str


class MigrationStatusResponse(BaseModel):
    """Статус миграции"""
    chrome_profiles_found: int
    camoufox_profiles_total: int
    migrated_profiles: int
    chrome_profiles: List[ChromeProfileInfo]
    migrated_profile_details: List[MigratedProfileDetail]


class GenerateMappingTemplateRequest(BaseModel):
    """Запрос на генерацию шаблона маппинга"""
    chrome_data_path: Optional[str] = None
    output_path: Optional[str] = None


class GenerateMappingTemplateResponse(BaseModel):
    """Ответ генерации шаблона маппинга"""
    success: bool
    template_path: str
    message: str


class ChromeDataPathsResponse(BaseModel):
    """Пути к данным Chrome"""
    current_os: str
    chrome_data_paths: Dict[str, str]
    recommended_path: str
    message: str 