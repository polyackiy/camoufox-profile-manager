"""
Генератор отпечатков браузера
"""

import random
from typing import Optional, Dict, Any
from .models import BrowserSettings


class FingerprintGenerator:
    """Генератор реалистичных отпечатков браузера"""
    
    def __init__(self):
        # Реалистичные комбинации операционных систем и разрешений
        self.os_screen_combinations = {
            "windows": ["1920x1080", "1366x768", "1536x864", "1440x900", "1600x900"],
            "macos": ["1440x900", "1680x1050", "1920x1080", "2560x1600", "2880x1800"],
            "linux": ["1920x1080", "1366x768", "1280x1024", "1600x900", "1440x900"]
        }
        
        # Языковые настройки по регионам
        self.language_sets = {
            "us": ["en-US", "en"],
            "uk": ["en-GB", "en"],
            "russia": ["ru-RU", "ru", "en-US"],
            "germany": ["de-DE", "de", "en"],
            "france": ["fr-FR", "fr", "en"],
            "spain": ["es-ES", "es", "en"],
            "italy": ["it-IT", "it", "en"],
            "china": ["zh-CN", "zh", "en"],
            "japan": ["ja-JP", "ja", "en"],
        }
        
        # User-Agent шаблоны для разных OS
        self.user_agent_templates = {
            "windows": [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{firefox_ver}.0) Gecko/20100101 Firefox/{firefox_ver}.0",
                "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:{firefox_ver}.0) Gecko/20100101 Firefox/{firefox_ver}.0",
            ],
            "macos": [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{firefox_ver}.0) Gecko/20100101 Firefox/{firefox_ver}.0",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver}.0.0.0 Safari/537.36",
            ],
            "linux": [
                "Mozilla/5.0 (X11; Linux x86_64; rv:{firefox_ver}.0) Gecko/20100101 Firefox/{firefox_ver}.0",
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:{firefox_ver}.0) Gecko/20100101 Firefox/{firefox_ver}.0",
            ]
        }
        
        # Актуальные версии браузеров
        self.firefox_versions = [130, 131, 132, 133, 134, 135]
        self.chrome_versions = [118, 119, 120, 121, 122, 123]
        
        # Характеристики железа
        self.hardware_specs = [
            {"cores": 2, "memory": 4},
            {"cores": 4, "memory": 8}, 
            {"cores": 6, "memory": 8},
            {"cores": 8, "memory": 16},
            {"cores": 12, "memory": 16},
            {"cores": 16, "memory": 32},
        ]

    async def generate_fingerprint(self, constraints: Optional[Dict[str, Any]] = None) -> BrowserSettings:
        """Генерирует реалистичный отпечаток браузера"""
        
        # Выбираем базовые параметры
        os_choice = constraints.get("os") if constraints else random.choice(["windows", "macos", "linux"])
        region = constraints.get("region") if constraints else random.choice(list(self.language_sets.keys()))
        
        # Выбираем разрешение экрана для выбранной OS
        screen = random.choice(self.os_screen_combinations[os_choice])
        
        # Языковые настройки
        languages = self.language_sets.get(region, ["en-US", "en"])
        
        # Характеристики железа
        hardware = random.choice(self.hardware_specs)
        
        # Генерируем User-Agent
        user_agent = self._generate_user_agent(os_choice)
        
        # Часовой пояс (можно улучшить логику выбора)
        timezone = self._get_timezone_for_region(region)
        
        # Геолокация (примерная для региона)
        geolocation = self._get_geolocation_for_region(region)
        
        return BrowserSettings(
            os=os_choice,
            screen=screen,
            user_agent=user_agent,
            languages=languages,
            timezone=timezone,
            geolocation=geolocation,
            hardware_concurrency=hardware["cores"],
            device_memory=hardware["memory"]
        )

    def _generate_user_agent(self, os_type: str) -> str:
        """Генерирует User-Agent для указанной OS"""
        template = random.choice(self.user_agent_templates[os_type])
        
        firefox_ver = random.choice(self.firefox_versions)
        chrome_ver = random.choice(self.chrome_versions)
        
        return template.format(
            firefox_ver=firefox_ver,
            chrome_ver=chrome_ver
        )

    def _get_timezone_for_region(self, region: str) -> str:
        """Возвращает часовой пояс для региона"""
        timezone_map = {
            "us": "America/New_York",
            "uk": "Europe/London", 
            "russia": "Europe/Moscow",
            "germany": "Europe/Berlin",
            "france": "Europe/Paris",
            "spain": "Europe/Madrid",
            "italy": "Europe/Rome",
            "china": "Asia/Shanghai",
            "japan": "Asia/Tokyo",
        }
        return timezone_map.get(region, "UTC")

    def _get_geolocation_for_region(self, region: str) -> Dict[str, float]:
        """Возвращает примерную геолокацию для региона"""
        coords_map = {
            "us": {"lat": 40.7128, "lon": -74.0060},  # Нью-Йорк
            "uk": {"lat": 51.5074, "lon": -0.1278},   # Лондон
            "russia": {"lat": 55.7558, "lon": 37.6176}, # Москва
            "germany": {"lat": 52.5200, "lon": 13.4050}, # Берлин
            "france": {"lat": 48.8566, "lon": 2.3522},  # Париж
            "spain": {"lat": 40.4168, "lon": -3.7038},  # Мадрид
            "italy": {"lat": 41.9028, "lon": 12.4964},  # Рим
            "china": {"lat": 31.2304, "lon": 121.4737}, # Шанхай
            "japan": {"lat": 35.6762, "lon": 139.6503}, # Токио
        }
        
        base_coords = coords_map.get(region, {"lat": 0, "lon": 0})
        
        # Добавляем небольшую случайность для уникальности
        lat_offset = random.uniform(-0.5, 0.5)
        lon_offset = random.uniform(-0.5, 0.5)
        
        return {
            "lat": base_coords["lat"] + lat_offset,
            "lon": base_coords["lon"] + lon_offset
        }

    async def rotate_fingerprint(self, current_settings: BrowserSettings) -> BrowserSettings:
        """Обновляет существующий отпечаток с сохранением некоторых характеристик"""
        
        # Иногда меняем только User-Agent, иногда полностью
        if random.random() < 0.3:  # 30% - полная смена
            return await self.generate_fingerprint()
        else:  # 70% - частичная смена
            new_ua = self._generate_user_agent(current_settings.os)
            current_settings.user_agent = new_ua
            
            # Возможно меняем разрешение экрана
            if random.random() < 0.2:  # 20% шанс
                current_settings.screen = random.choice(
                    self.os_screen_combinations[current_settings.os]
                )
            
            return current_settings 