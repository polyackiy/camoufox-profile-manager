#!/usr/bin/env python3
"""
Демонстрация CamoufoxProfileManager с полноценной базой данных SQLite
"""

import asyncio
import time
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

# Импортируем компоненты системы
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox_pm.core.models import Profile, ProxyConfig, ProxyType, ProfileGroup
from camoufox_pm.core.profile_manager import ProfileManager
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.fingerprint_generator import FingerprintGenerator


async def demo_database_operations():
    """Демонстрация операций с базой данных"""
    print("🎯 === Демонстрация CamoufoxProfileManager с SQLite ===\n")
    
    # Инициализируем систему с базой данных
    storage = StorageManager("demo_data/profiles.db")
    await storage.initialize()
    
    manager = ProfileManager(storage, "demo_data")
    await manager.initialize()
    
    print("✅ База данных SQLite инициализирована\n")
    
    print("1️⃣ Создание групп профилей...")
    
    # Создаем группы профилей
    groups = [
        ProfileGroup(name="Социальные сети", description="Профили для работы с соцсетями"),
        ProfileGroup(name="E-commerce", description="Профили для интернет-магазинов"),
        ProfileGroup(name="Исследования", description="Профили для парсинга и исследований"),
    ]
    
    for group in groups:
        await storage.save_profile_group(group)
        print(f"   📁 Создана группа: {group.name}")
    
    print(f"✅ Создано {len(groups)} групп\n")
    
    print("2️⃣ Создание профилей...")
    
    # Создаем профили в разных группах
    profiles_data = [
        {
            "name": "Facebook Профиль 1",
            "group": groups[0].id,
            "os": "windows",
            "proxy": {
                "type": "http",
                "server": "proxy1.example.com:8080",
                "username": "user1",
                "password": "pass1"
            }
        },
        {
            "name": "Amazon Профиль 1",
            "group": groups[1].id,
            "os": "macos",
            "proxy": {
                "type": "socks5",
                "server": "proxy2.example.com:1080",
                "username": "user2",
                "password": "pass2"
            }
        },
        {
            "name": "Research Bot 1",
            "group": groups[2].id,
            "os": "linux",
            "proxy": None
        },
        {
            "name": "Instagram Профиль 1",
            "group": groups[0].id,
            "os": "windows",
            "proxy": None
        },
        {
            "name": "eBay Профиль 1",
            "group": groups[1].id,
            "os": "macos",
            "proxy": {
                "type": "http",
                "server": "proxy3.example.com:3128"
            }
        }
    ]
    
    created_profiles = []
    for profile_data in profiles_data:
        profile = await manager.create_profile(
            name=profile_data["name"],
            group=profile_data["group"],
            browser_settings={"os": profile_data["os"]},
            proxy_config=profile_data["proxy"]
        )
        created_profiles.append(profile)
        print(f"   👤 Создан профиль: {profile.name}")
    
    print(f"✅ Создано {len(created_profiles)} профилей\n")
    
    print("3️⃣ Статистика по группам:")
    all_groups = await storage.list_profile_groups()
    for group in all_groups:
        print(f"   📊 {group.name}: {group.profile_count} профилей")
    print()
    
    print("4️⃣ Фильтрация профилей...")
    
    # Фильтрация по группе
    social_profiles = await manager.list_profiles(filters={"group": groups[0].id})
    print(f"   📱 Профили социальных сетей: {len(social_profiles)}")
    for profile in social_profiles:
        print(f"      - {profile.name}")
    
    # Фильтрация по статусу
    active_profiles = await manager.list_profiles(filters={"status": "active"})
    print(f"   ✅ Активные профили: {len(active_profiles)}")
    
    # Поиск по имени
    facebook_profiles = await manager.list_profiles(filters={"name_like": "Facebook"})
    print(f"   🔍 Профили с 'Facebook' в названии: {len(facebook_profiles)}")
    print()
    
    print("5️⃣ Операции с профилями...")
    
    # Обновление профиля
    test_profile = created_profiles[0]
    await manager.update_profile(test_profile.id, {
        "notes": "Обновлен через демо базы данных",
        "status": "inactive"
    })
    print(f"   🔄 Обновлен профиль: {test_profile.name}")
    
    # Клонирование профиля
    cloned_profile = await manager.clone_profile(
        test_profile.id,
        "Клон Facebook Профиля",
        regenerate_fingerprint=True
    )
    print(f"   📋 Клонирован профиль: {cloned_profile.name}")
    
    # Экспорт профиля
    export_data = await manager.export_profile(test_profile.id)
    print(f"   💾 Экспортирован профиль: {len(export_data)} байт")
    print()
    
    print("6️⃣ Статистика использования:")
    
    # Получаем статистику для первого профиля
    profile_stats = await storage.get_profile_usage_stats(test_profile.id)
    print(f"   📈 Записей статистики для {test_profile.name}: {len(profile_stats)}")
    
    for stat in profile_stats[-3:]:  # Последние 3 записи
        print(f"      {stat.timestamp.strftime('%H:%M:%S')} - {stat.action}")
    print()
    
    print("7️⃣ Пагинация...")
    
    # Пагинация профилей
    page1 = await manager.list_profiles(limit=3, offset=0)
    page2 = await manager.list_profiles(limit=3, offset=3)
    
    print(f"   📄 Страница 1 (3 профиля): {[p.name for p in page1]}")
    print(f"   📄 Страница 2 (3 профиля): {[p.name for p in page2]}")
    
    total_count = await storage.count_profiles()
    print(f"   📊 Всего профилей в БД: {total_count}")
    print()
    
    print("8️⃣ Тестирование браузера с профилем из БД...")
    await demo_browser_with_db_profile(created_profiles[2])  # Research Bot
    
    print("9️⃣ Очистка тестовых данных...")
    
    # Удаляем тестовые профили
    for profile in created_profiles + [cloned_profile]:
        await manager.delete_profile(profile.id)
        print(f"   🗑️ Удален профиль: {profile.name}")
    
    # Очищаем группы
    for group in groups:
        await storage.db.delete_profile_group(group.id)
        print(f"   🗑️ Удалена группа: {group.name}")
    
    print("✅ Тестовые данные очищены")
    
    # Закрываем базу данных
    await storage.close()
    print("\n🎉 === Демонстрация базы данных завершена успешно! ===")


async def demo_browser_with_db_profile(profile: Profile):
    """Тест запуска браузера с профилем из базы данных"""
    print(f"   🚀 Запуск браузера с профилем: {profile.name}")
    
    try:
        # Конвертируем настройки профиля в формат Camoufox
        camoufox_options = profile.to_camoufox_launch_options()
        camoufox_options["geoip"] = False
        camoufox_options["headless"] = True  # Headless для демо
        camoufox_options["humanize"] = True
        
        async with AsyncCamoufox(**camoufox_options) as browser:
            page = await browser.new_page()
            
            # Переходим на тестовый сайт
            await page.goto("https://httpbin.org/user-agent")
            
            # Получаем User-Agent
            content = await page.content()
            if "Mozilla" in content:
                print(f"   ✅ Браузер работает, User-Agent установлен")
            
            # Тестируем localStorage
            await page.evaluate("""
                localStorage.setItem('db_profile_test', JSON.stringify({
                    profileId: '%s',
                    dbTest: true,
                    timestamp: new Date().toISOString()
                }));
            """ % profile.id)
            
            # Проверяем что данные сохранились
            stored_data = await page.evaluate("localStorage.getItem('db_profile_test')")
            if stored_data:
                print(f"   💾 localStorage работает корректно")
            
            print(f"   ✅ Профиль {profile.name} успешно протестирован")
        
    except Exception as e:
        print(f"   ❌ Ошибка тестирования браузера: {e}")


async def demo_performance_test():
    """Тест производительности базы данных"""
    print("\n🚀 === Тест производительности БД ===\n")
    
    storage = StorageManager("demo_data/performance_test.db")
    await storage.initialize()
    
    manager = ProfileManager(storage, "demo_data")
    await manager.initialize()
    
    # Создаем группу
    group = ProfileGroup(name="Performance Test", description="Тестовая группа")
    await storage.save_profile_group(group)
    
    print("📊 Создание 50 профилей...")
    start_time = time.time()
    
    profiles = []
    for i in range(50):
        profile = await manager.create_profile(
            name=f"Test Profile {i+1}",
            group=group.id,
            browser_settings={"os": ["windows", "macos", "linux"][i % 3]}
        )
        profiles.append(profile)
        
        if (i + 1) % 10 == 0:
            print(f"   ✅ Создано {i+1} профилей")
    
    create_time = time.time() - start_time
    print(f"⏱️ Время создания 50 профилей: {create_time:.2f} секунд")
    
    # Тест поиска
    print("\n🔍 Тест поиска...")
    start_time = time.time()
    
    # Поиск по группе
    group_profiles = await manager.list_profiles(filters={"group": group.id})
    
    # Поиск по статусу
    active_profiles = await manager.list_profiles(filters={"status": "active"})
    
    # Поиск по имени
    test_profiles = await manager.list_profiles(filters={"name_like": "Test"})
    
    search_time = time.time() - start_time
    print(f"⏱️ Время выполнения поисковых запросов: {search_time:.3f} секунд")
    print(f"   📊 Найдено: группа={len(group_profiles)}, активные={len(active_profiles)}, тест={len(test_profiles)}")
    
    # Тест массовых операций
    print("\n🔄 Тест массовых операций...")
    start_time = time.time()
    
    # Массовое обновление
    profile_ids = [p.id for p in profiles[:20]]
    await manager.bulk_update_profiles(profile_ids, {"notes": "Массово обновлен"})
    
    # Массовое удаление
    for profile in profiles:
        await manager.delete_profile(profile.id)
    
    bulk_time = time.time() - start_time
    print(f"⏱️ Время массовых операций: {bulk_time:.2f} секунд")
    
    # Очистка
    await storage.db.delete_profile_group(group.id)
    await storage.close()
    
    # Удаляем тестовую БД
    db_path = Path("demo_data/performance_test.db")
    if db_path.exists():
        db_path.unlink()
    
    print("✅ Тест производительности завершен")


async def main():
    """Главная функция демонстрации"""
    try:
        # Основная демонстрация БД
        await demo_database_operations()
        
        # Тест производительности
        await demo_performance_test()
        
        print("\n📋 Что было продемонстрировано:")
        print("   ✅ Полноценная база данных SQLite")
        print("   ✅ Создание и управление группами профилей") 
        print("   ✅ CRUD операции с профилями")
        print("   ✅ Фильтрация и поиск профилей")
        print("   ✅ Статистика использования")
        print("   ✅ Пагинация результатов")
        print("   ✅ Тестирование браузера с БД")
        print("   ✅ Тест производительности (50 профилей)")
        print("   ✅ Персистентность данных между сессиями")
        
        print("\n🚀 Следующие этапы:")
        print("   🌐 REST API для веб-интерфейса")
        print("   🎨 Создание веб-интерфейса") 
        print("   🔒 Система авторизации")
        print("   📊 Расширенная аналитика")
        print("   🐳 Docker контейнеризация")
        
    except Exception as e:
        print(f"❌ Ошибка в демонстрации: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main()) 