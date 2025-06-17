"""
Модели данных для системы управления профилями Camoufox
"""

from datetime import datetime
from typing import Optional, Dict, List, Any
from enum import Enum
from pydantic import BaseModel, Field
import uuid
import string
import random
import time


def generate_short_id(length: int = 8) -> str:
    """
    Генерирует короткий ID из букв и цифр
    Использует timestamp для обеспечения уникальности и порядка
    """
    # Используем буквы и цифры (исключаем похожие символы)
    chars = string.ascii_lowercase + string.digits
    # Убираем похожие символы для лучшей читаемости
    chars = chars.replace('0', '').replace('o', '').replace('1', '').replace('l', '').replace('i', '')
    
    # Получаем timestamp в микросекундах для уникальности
    timestamp = int(time.time() * 1000000)
    
    # Конвертируем timestamp в base36 и берем последние символы
    base36_time = ''
    temp_time = timestamp
    while temp_time > 0 and len(base36_time) < length - 2:
        base36_time = chars[temp_time % len(chars)] + base36_time
        temp_time //= len(chars)
    
    # Дополняем случайными символами до нужной длины
    while len(base36_time) < length:
        base36_time += random.choice(chars)
    
    return base36_time[:length]


def generate_profile_id() -> str:
    """Генерирует короткий ID для профиля (8 символов)"""
    return generate_short_id(8)


def generate_group_id() -> str:
    """Генерирует короткий ID для группы (8 символов)"""
    return generate_short_id(8)


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


class WebRTCMode(str, Enum):
    """Режим WebRTC IP Spoofing"""
    FORWARD = "forward"    # Передавать реальный IP
    REPLACE = "replace"    # Заменить на IP прокси
    REAL = "real"         # Использовать реальный IP
    NONE = "none"         # Отключить WebRTC


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
    # Базовые настройки
    os: str = "windows"  # windows, linux, macos
    screen: str = "1920x1080"
    user_agent: Optional[str] = None
    languages: List[str] = ["en-US", "en"]
    timezone: Optional[str] = None
    locale: Optional[str] = None  # Основная локаль (ru_RU, en_US)
    
    # Размер окна браузера
    window_width: Optional[int] = 1280  # Ширина окна браузера
    window_height: Optional[int] = 720  # Высота окна браузера
    
    # Геолокация
    geolocation: Optional[Dict[str, float]] = None
    
    # WebRTC настройки
    webrtc_mode: WebRTCMode = WebRTCMode.REPLACE
    webrtc_public_ip: Optional[str] = None
    webrtc_local_ips: Optional[List[str]] = None
    
    # Canvas и WebGL fingerprinting
    canvas_noise: bool = True
    webgl_noise: bool = True
    webgl_vendor: Optional[str] = None
    webgl_renderer: Optional[str] = None
    
    # Аудио контекст
    audio_noise: bool = True
    audio_context_sample_rate: Optional[int] = None
    
    # Характеристики устройства
    hardware_concurrency: Optional[int] = None
    device_memory: Optional[int] = None
    max_touch_points: int = 0
    
    # Шрифты
    fonts: Optional[List[str]] = None
    
    # Дополнительные HTTP заголовки
    accept_language: Optional[str] = None
    accept_encoding: Optional[str] = None
    
    def to_camoufox_config(self) -> Dict[str, Any]:
        """Преобразование в конфигурацию Camoufox (только config-параметры)"""
        config = {}
        
        # Пока оставляем config минимальным, так как большинство параметров 
        # должны передаваться как основные параметры Camoufox
        
        return config


class Profile(BaseModel):
    """Модель профиля браузера"""
    id: str = Field(default_factory=generate_profile_id)
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
    
    class Config:
        use_enum_values = True

    def get_storage_path(self, base_path: str = "data/profiles") -> str:
        """Получить путь для сохранения данных профиля"""
        if not self.storage_path:
            self.storage_path = f"{base_path}/profile_{self.id}"
        return self.storage_path

    def to_camoufox_launch_options(self) -> Dict[str, Any]:
        """Преобразование в параметры запуска Camoufox"""
        # Получаем конфигурацию браузера (только config-параметры)
        config = self.browser_settings.to_camoufox_config()
        
        # Формируем основные параметры для AsyncCamoufox
        options = {
            "os": self.browser_settings.os,
            "locale": ",".join(self.browser_settings.languages),
            "config": config,  # Конфигурация передается через параметр config
            "user_data_dir": self.get_storage_path(),
            "persistent_context": True,
            "headless": False,
            "humanize": True,
            "i_know_what_im_doing": True  # Отключаем предупреждения
        }
        
        # Добавляем размер окна
        if self.browser_settings.window_width and self.browser_settings.window_height:
            options["window"] = (self.browser_settings.window_width, self.browser_settings.window_height)
        
        # Добавляем геолокацию
        if self.browser_settings.geolocation:
            options["geoip"] = False  # Отключаем автоматическое определение
            # Для Camoufox используем config для геолокации
            config["geolocation:latitude"] = self.browser_settings.geolocation["lat"]
            config["geolocation:longitude"] = self.browser_settings.geolocation["lon"]
            if "accuracy" in self.browser_settings.geolocation:
                config["geolocation:accuracy"] = self.browser_settings.geolocation["accuracy"]
        else:
            options["geoip"] = True  # Автоматическое определение на основе IP
        
        # Добавляем прокси если настроен
        if self.proxy:
            options["proxy"] = self.proxy.to_camoufox_format()
            
        return options


class ProfileGroup(BaseModel):
    """Группа профилей"""
    id: str = Field(default_factory=generate_group_id)
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