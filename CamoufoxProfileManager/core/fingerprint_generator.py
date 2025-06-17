"""
Генератор отпечатков браузера
"""

import random
from typing import Optional, Dict, Any
from .models import BrowserSettings, WebRTCMode


class FingerprintGenerator:
    """Генератор реалистичных отпечатков браузера"""
    
    def __init__(self):
        # Реалистичные комбинации операционных систем и разрешений
        self.os_screen_combinations = {
            "windows": ["1920x1080", "1366x768", "1536x864", "1440x900", "1600x900", "2560x1440"],
            "macos": ["1440x900", "1680x1050", "1920x1080", "2560x1600", "2880x1800", "3840x2160"],
            "linux": ["1920x1080", "1366x768", "1280x1024", "1600x900", "1440x900", "2560x1440"]
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
        
        # Локали для регионов
        self.locale_map = {
            "us": "en_US",
            "uk": "en_GB", 
            "russia": "ru_RU",
            "germany": "de_DE",
            "france": "fr_FR",
            "spain": "es_ES",
            "italy": "it_IT",
            "china": "zh_CN",
            "japan": "ja_JP",
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
        
        # WebRTC конфигурации
        self.webrtc_modes = [
            WebRTCMode.REPLACE,  # Самый популярный для антидетекта
            WebRTCMode.NONE,     # Второй по популярности
            WebRTCMode.FORWARD,  # Реже используется
            WebRTCMode.REAL      # Редко используется
        ]
        
        # WebGL vendor/renderer комбинации
        self.webgl_configs = [
            {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)"},
            {"vendor": "Google Inc. (Intel)", "renderer": "ANGLE (Intel, Intel(R) HD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
            {"vendor": "Google Inc. (AMD)", "renderer": "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
            {"vendor": "Mozilla", "renderer": "Mozilla -- (Adreno (TM) 640)"},
            {"vendor": "WebKit", "renderer": "WebKit WebGL"},
        ]
        
        # Аудио контекст настройки
        self.audio_sample_rates = [44100, 48000, 96000]

    async def generate_fingerprint(self, constraints: Optional[Dict[str, Any]] = None) -> BrowserSettings:
        """Генерирует реалистичный отпечаток браузера"""
        
        # Если constraints - это объект BrowserSettings, конвертируем в словарь
        if hasattr(constraints, 'dict'):
            constraints = constraints.dict()
        
        # Выбираем базовые параметры
        os_choice = constraints.get("os") if constraints else random.choice(["windows", "macos", "linux"])
        region = constraints.get("region") if constraints else random.choice(list(self.language_sets.keys()))
        
        # Выбираем разрешение экрана для выбранной OS
        screen = random.choice(self.os_screen_combinations[os_choice])
        
        # Языковые настройки
        languages = self.language_sets.get(region, ["en-US", "en"])
        locale = self.locale_map.get(region, "en_US")
        
        # Характеристики железа
        hardware = random.choice(self.hardware_specs)
        
        # Генерируем User-Agent
        user_agent = self._generate_user_agent(os_choice)
        
        # Часовой пояс
        timezone = self._get_timezone_for_region(region)
        
        # Геолокация
        geolocation = self._get_geolocation_for_region(region)
        
        # WebRTC режим (с весами - чаще используется replace)
        webrtc_mode = random.choices(
            self.webrtc_modes,
            weights=[0.5, 0.3, 0.15, 0.05]  # replace, none, forward, real
        )[0]
        
        # WebGL конфигурация
        webgl_config = random.choice(self.webgl_configs)
        
        # Touch points (0 для desktop, 1-10 для мобильных)
        max_touch_points = 0 if os_choice in ["windows", "macos", "linux"] else random.randint(1, 10)
        
        return BrowserSettings(
            # Базовые настройки
            os=os_choice,
            screen=screen,
            user_agent=user_agent,
            languages=languages,
            timezone=timezone,
            locale=locale,
            geolocation=geolocation,
            
            # WebRTC
            webrtc_mode=webrtc_mode,
            webrtc_public_ip=None,  # Будет заполнено при использовании прокси
            webrtc_local_ips=None,
            
            # Canvas и WebGL
            canvas_noise=random.choice([True, False]),  # 50/50
            webgl_noise=random.choice([True, False]),   # 50/50
            webgl_vendor=webgl_config["vendor"],
            webgl_renderer=webgl_config["renderer"],
            
            # Аудио
            audio_noise=random.choice([True, False]),
            audio_context_sample_rate=random.choice(self.audio_sample_rates),
            
            # Устройство
            hardware_concurrency=hardware["cores"],
            device_memory=hardware["memory"],
            max_touch_points=max_touch_points,
            
            # HTTP заголовки
            accept_language=",".join(languages),
            accept_encoding="gzip, deflate, br"
        )

    async def reset_fingerprint(self, profile_id: str) -> BrowserSettings:
        """Полная регенерация отпечатка профиля"""
        return await self.generate_fingerprint()

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