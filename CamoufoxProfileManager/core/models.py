"""
Модели данных для системы управления профилями Camoufox
"""

from datetime import datetime
from typing import Optional, Dict, List, Any
from enum import Enum
from pydantic import BaseModel, Field
import uuid


class ProfileStatus(str, Enum):
    """Статус профиля"""
    ACTIVE = "active"
    INACTIVE = "inactive" 
    BLOCKED = "blocked"
    MAINTENANCE = "maintenance"


class ProxyType(str, Enum):
    """Тип прокси"""
    HTTP = "http"
    HTTPS = "https"
    SOCKS4 = "socks4"
    SOCKS5 = "socks5"


class ProxyConfig(BaseModel):
    """Конфигурация прокси"""
    type: ProxyType
    server: str
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    
    def to_camoufox_format(self) -> Dict[str, str]:
        """Преобразование в формат Camoufox"""
        config = {"server": f"{self.type.value}://{self.server}"}
        if self.username:
            config["username"] = self.username
        if self.password:
            config["password"] = self.password
        return config


class BrowserSettings(BaseModel):
    """Настройки браузера"""
    os: str = "windows"  # windows, linux, macos
    screen: str = "1920x1080"
    user_agent: Optional[str] = None
    languages: List[str] = ["en-US", "en"]
    timezone: Optional[str] = None
    geolocation: Optional[Dict[str, float]] = None
    fonts: Optional[List[str]] = None
    webgl_vendor: Optional[str] = None
    webgl_renderer: Optional[str] = None
    hardware_concurrency: Optional[int] = None
    device_memory: Optional[int] = None
    
    def to_camoufox_config(self) -> Dict[str, Any]:
        """Преобразование в конфигурацию Camoufox"""
        # Используем только базовые параметры, позволяя Camoufox генерировать остальное
        config = {
            "os": self.os,
            "locale": ",".join(self.languages),
            "i_know_what_im_doing": True  # Отключаем предупреждения
        }
        
        # Примечание: timezone передается через другие механизмы Camoufox
        # if self.timezone:
        #     config["timezone"] = self.timezone
            
        return config


class Profile(BaseModel):
    """Модель профиля браузера"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    group: Optional[str] = None
    status: ProfileStatus = ProfileStatus.ACTIVE
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_used: Optional[datetime] = None
    
    browser_settings: BrowserSettings = Field(default_factory=BrowserSettings)
    proxy: Optional[ProxyConfig] = None
    extensions: List[str] = Field(default_factory=list)
    storage_path: Optional[str] = None
    notes: Optional[str] = None
    
    # Дополнительные настройки
    auto_rotate_fingerprint: bool = False
    rotate_interval_hours: int = 24
    max_sessions_per_day: int = 100
    
    class Config:
        use_enum_values = True

    def get_storage_path(self, base_path: str = "data/profiles") -> str:
        """Получить путь для сохранения данных профиля"""
        if not self.storage_path:
            self.storage_path = f"{base_path}/profile_{self.id}"
        return self.storage_path

    def to_camoufox_launch_options(self) -> Dict[str, Any]:
        """Преобразование в параметры запуска Camoufox"""
        options = self.browser_settings.to_camoufox_config()
        
        if self.proxy:
            options["proxy"] = self.proxy.to_camoufox_format()
            
        # Добавляем путь к данным профиля
        options["user_data_dir"] = self.get_storage_path()
        options["persistent_context"] = True
        
        return options


class ProfileGroup(BaseModel):
    """Группа профилей"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    profile_count: int = 0


class UsageStats(BaseModel):
    """Статистика использования профиля"""
    id: Optional[int] = None
    profile_id: str
    action: str
    timestamp: datetime = Field(default_factory=datetime.now)
    duration: Optional[int] = None  # в секундах
    success: bool = True
    details: Optional[Dict[str, Any]] = None


class ProxyTestResult(BaseModel):
    """Результат тестирования прокси"""
    proxy_id: str
    success: bool
    response_time: Optional[int] = None  # в миллисекундах
    error_message: Optional[str] = None
    ip_address: Optional[str] = None
    country: Optional[str] = None
    tested_at: datetime = Field(default_factory=datetime.now)


class SystemStatus(BaseModel):
    """Статус системы"""
    total_profiles: int
    active_profiles: int
    running_browsers: int
    total_groups: int
    system_load: float
    memory_usage: float
    disk_usage: float
    uptime_seconds: int 