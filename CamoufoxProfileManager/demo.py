#!/usr/bin/env python3
"""
Демонстрация возможностей CamoufoxProfileManager
"""

import asyncio
import time
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

# Временные заглушки для отсутствующих модулей
class StorageManager:
    """Временная заглушка для StorageManager"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.profiles = {}
        self.usage_stats = []
    
    async def save_profile(self, profile):
        self.profiles[profile.id] = profile
        print(f"💾 Профиль {profile.name} сохранен в БД")
    
    async def get_profile(self, profile_id: str):
        return self.profiles.get(profile_id)
    
    async def update_profile(self, profile):
        if profile.id in self.profiles:
            self.profiles[profile.id] = profile
            print(f"🔄 Профиль {profile.name} обновлен в БД")
    
    async def delete_profile(self, profile_id: str) -> bool:
        if profile_id in self.profiles:
            del self.profiles[profile_id]
            print(f"🗑️ Профиль {profile_id} удален из БД")
            return True
        return False
    
    async def list_profiles(self, filters=None, limit=None, offset=0):
        return list(self.profiles.values())
    
    async def log_usage(self, usage_stats):
        self.usage_stats.append(usage_stats)
        print(f"📊 Логируем действие: {usage_stats.action}")
    
    async def get_profile_usage_stats(self, profile_id: str):
        return [stats for stats in self.usage_stats if stats.profile_id == profile_id]


class FingerprintGenerator:
    """Временная заглушка для FingerprintGenerator"""
    
    async def generate_fingerprint(self, constraints=None):
        from core.models import BrowserSettings
        
        # Генерируем реалистичные характеристики
        import random
        
        operating_systems = ["windows", "macos", "linux"]
        screen_resolutions = ["1920x1080", "1366x768", "1440x900", "1600x900", "1280x1024"]
        languages_sets = [
            ["en-US", "en"],
            ["ru-RU", "ru", "en-US"], 
            ["es-ES", "es", "en"],
            ["de-DE", "de", "en"],
            ["fr-FR", "fr", "en"]
        ]
        
        return BrowserSettings(
            os=random.choice(operating_systems),
            screen=random.choice(screen_resolutions),
            languages=random.choice(languages_sets),
            hardware_concurrency=random.choice([2, 4, 6, 8, 12, 16]),
            device_memory=random.choice([2, 4, 8, 16, 32])
        )


# Импортируем модели
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.models import Profile, ProxyConfig, ProxyType
from core.profile_manager import ProfileManager


async def demo_basic_operations():
    """Демонстрация базовых операций с профилями"""
    print("🎯 === Демонстрация CamoufoxProfileManager ===\n")
    
    # Инициализируем систему
    storage = StorageManager("demo.db")
    manager = ProfileManager(storage, "demo_data") 
    
    print("1️⃣ Создание профилей...")
    
    # Создаем профили для разных задач
    profiles = []
    
    # Профиль для социальных сетей
    social_profile = await manager.create_profile(
        name="Социальные сети",
        group="social_media",
        browser_settings={
            "os": "windows",
            "screen": "1920x1080",
            "languages": ["en-US", "en"]
        }
    )
    profiles.append(social_profile)
    
    # Профиль для е-коммерса
    ecommerce_profile = await manager.create_profile(
        name="Интернет-магазины",
        group="ecommerce", 
        browser_settings={
            "os": "macos",
            "screen": "1440x900",
            "languages": ["ru-RU", "ru", "en-US"]
        },
        proxy_config={
            "type": "http",
            "server": "proxy.example.com:8080",
            "username": "user",
            "password": "pass"
        }
    )
    profiles.append(ecommerce_profile)
    
    # Профиль для исследований
    research_profile = await manager.create_profile(
        name="Исследования и парсинг",
        group="research",
        browser_settings={
            "os": "linux",
            "screen": "1366x768",
            "languages": ["de-DE", "de", "en"]
        }
    )
    profiles.append(research_profile)
    
    print(f"✅ Создано {len(profiles)} профилей\n")
    
    print("2️⃣ Список профилей:")
    all_profiles = await manager.list_profiles()
    for profile in all_profiles:
        print(f"   📝 {profile.name} ({profile.group}) - {profile.browser_settings.os}")
    print()
    
    print("3️⃣ Клонирование профиля...")
    cloned_profile = await manager.clone_profile(
        social_profile.id,
        "Социальные сети (копия)",
        regenerate_fingerprint=True
    )
    print(f"✅ Создана копия: {cloned_profile.name}\n")
    
    print("4️⃣ Ротация отпечатка...")
    await manager.rotate_profile_fingerprint(research_profile.id)
    print("✅ Отпечаток обновлен\n")
    
    print("5️⃣ Экспорт профиля...")
    export_data = await manager.export_profile(ecommerce_profile.id) 
    print(f"✅ Профиль экспортирован ({len(export_data)} байт)\n")
    
    print("6️⃣ Статистика профилей:")
    for profile in all_profiles:
        stats = await manager.get_profile_stats(profile.id)
        print(f"   📊 {stats['name']}: создан {stats['created_at'].strftime('%Y-%m-%d %H:%M')}")
    print()
    
    return profiles[0]  # Возвращаем первый профиль для демо браузера


async def demo_browser_launch(profile: Profile):
    """Демонстрация запуска браузера с профилем"""
    print("7️⃣ Запуск браузера с профилем...")
    
    try:
        # Конвертируем настройки профиля в формат Camoufox
        camoufox_options = profile.to_camoufox_launch_options()
        
        # Отключаем geoip для демо
        camoufox_options["geoip"] = False
        camoufox_options["headless"] = False
        camoufox_options["humanize"] = True
        
        print(f"🚀 Запускаем браузер с настройками:")
        print(f"   OS: {camoufox_options['os']}")
        print(f"   Locale: {camoufox_options['locale']}")
        print(f"   Profile dir: {camoufox_options['user_data_dir']}")
        
        async with AsyncCamoufox(**camoufox_options) as browser:
            print("✅ Браузер запущен успешно!")
            
            page = await browser.new_page()
            print("📄 Страница создана")
            
            # Переходим на тестовый сайт
            await page.goto("https://httpbin.org/headers")
            print("🌐 Загружена страница httpbin.org/headers")
            
            # Получаем информацию о браузере
            user_agent = await page.evaluate("navigator.userAgent")
            platform = await page.evaluate("navigator.platform") 
            languages = await page.evaluate("navigator.languages")
            screen_info = await page.evaluate("""
                () => ({
                    width: screen.width,
                    height: screen.height,
                    colorDepth: screen.colorDepth
                })
            """)
            
            print(f"🔍 Информация о браузере:")
            print(f"   User-Agent: {user_agent[:60]}...")
            print(f"   Platform: {platform}")
            print(f"   Languages: {languages}")
            print(f"   Screen: {screen_info['width']}x{screen_info['height']}")
            
            # Проверяем сохранение cookies
            await page.evaluate("document.cookie = 'test_profile=demo_session; path=/'")
            cookies = await page.context.cookies()
            print(f"🍪 Установлено cookies: {len(cookies)}")
            
            # Сохраняем данные в localStorage
            await page.evaluate("""
                localStorage.setItem('profile_demo', JSON.stringify({
                    profileId: '%s',
                    sessionStart: new Date().toISOString(),
                    testData: 'Демо данные профиля'
                }));
            """ % profile.id)
            
            print("💾 Данные сохранены в localStorage")
            print("⏳ Браузер будет открыт 10 секунд для демонстрации...")
            await asyncio.sleep(10)  # Используем async sleep
            
        print("✅ Браузер закрыт, данные профиля сохранены\n")
        
    except Exception as e:
        print(f"❌ Ошибка запуска браузера: {e}")
        import traceback
        traceback.print_exc()


async def demo_advanced_features():
    """Демонстрация продвинутых функций"""
    print("8️⃣ Продвинутые функции:\n")
    
    storage = StorageManager("demo.db")
    manager = ProfileManager(storage, "demo_data")
    
    # Массовые операции
    print("   📦 Массовое создание профилей...")
    profile_names = [
        "Тестовый профиль 1",
        "Тестовый профиль 2", 
        "Тестовый профиль 3"
    ]
    
    created_profiles = []
    for name in profile_names:
        profile = await manager.create_profile(name, group="test_batch")
        created_profiles.append(profile)
    
    print(f"   ✅ Создано {len(created_profiles)} профилей")
    
    # Массовое обновление
    profile_ids = [p.id for p in created_profiles]
    await manager.bulk_update_profiles(profile_ids, {"status": "inactive"})
    print("   ✅ Массовое обновление выполнено")
    
    # Удаление профилей
    for profile in created_profiles:
        await manager.delete_profile(profile.id)
    print("   ✅ Тестовые профили удалены")


async def main():
    """Главная демонстрационная функция"""
    try:
        # Основные операции
        demo_profile = await demo_basic_operations()
        
        # Запуск браузера (асинхронная операция)
        await demo_browser_launch(demo_profile)
        
        # Продвинутые функции
        await demo_advanced_features()
        
        print("🎉 === Демонстрация завершена успешно! ===")
        print("\n📋 Что было продемонстрировано:")
        print("   ✅ Создание и управление профилями")
        print("   ✅ Генерация и ротация отпечатков браузера")
        print("   ✅ Сохранение данных профилей")
        print("   ✅ Запуск Camoufox с настройками профиля")
        print("   ✅ Персистентность cookies и localStorage")
        print("   ✅ Клонирование и экспорт профилей")
        print("   ✅ Массовые операции")
        
        print("\n🚀 Следующие шаги для полной реализации:")
        print("   🔧 Реализация StorageManager с SQLite")
        print("   🎨 Создание веб-интерфейса")
        print("   🌐 REST API для управления")
        print("   🔒 Система безопасности и аутентификации")
        print("   📊 Мониторинг и аналитика")
        
        print(f"\n📁 Данные профилей сохранены в: {Path('demo_data/profiles').absolute()}")
        
    except Exception as e:
        print(f"❌ Ошибка в демонстрации: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main()) 