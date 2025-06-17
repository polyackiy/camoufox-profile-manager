#!/usr/bin/env python3
"""
Изучение возможностей Camoufox API для создания системы профилей
"""

from camoufox.sync_api import Camoufox
import json
import os
import time
from pathlib import Path

def explore_camoufox_options():
    """Изучаем доступные опции Camoufox"""
    print("=== Изучение опций Camoufox ===")
    
    # Базовые опции
    basic_options = {
        'os': 'macos',  # windows, linux, macos
        'headless': False,
        'geoip': True,
        'screen': '1920x1080',
        'proxy': None,  # можно добавить прокси
    }
    
    print(f"Базовые опции: {json.dumps(basic_options, indent=2)}")
    
    # Пробуем создать браузер с базовыми настройками
    try:
        with Camoufox(**basic_options) as browser:
            print("✅ Браузер успешно создан")
            
            # Создаем страницу
            page = browser.new_page()
            print("✅ Страница создана")
            
            # Тестируем получение информации о браузере
            user_agent = page.evaluate("navigator.userAgent")
            print(f"User Agent: {user_agent}")
            
            platform = page.evaluate("navigator.platform")
            print(f"Platform: {platform}")
            
            screen_info = page.evaluate("""
                () => ({
                    width: screen.width,
                    height: screen.height,
                    colorDepth: screen.colorDepth,
                    pixelDepth: screen.pixelDepth
                })
            """)
            print(f"Screen info: {json.dumps(screen_info, indent=2)}")
            
            # Тестируем геолокацию
            if basic_options['geoip']:
                geo_info = page.evaluate("""
                    new Promise((resolve) => {
                        navigator.geolocation.getCurrentPosition(
                            (position) => resolve({
                                latitude: position.coords.latitude,
                                longitude: position.coords.longitude
                            }),
                            (error) => resolve({error: error.message})
                        );
                    })
                """)
                print(f"Geo info: {json.dumps(geo_info, indent=2)}")
            
            # Тестируем сохранение cookies
            page.goto("https://httpbin.org/cookies/set/test/12345")
            time.sleep(2)
            
            cookies = page.context.cookies()
            print(f"Cookies: {json.dumps([c for c in cookies], indent=2)}")
            
            print("✅ Базовое тестирование завершено")
            
    except Exception as e:
        print(f"❌ Ошибка при создании браузера: {e}")

def test_profile_persistence():
    """Тестируем возможность сохранения профиля"""
    print("\n=== Тестирование сохранения профиля ===")
    
    profile_dir = Path("./test_profile")
    profile_dir.mkdir(exist_ok=True)
    
    # Создаем браузер с пользовательской директорией
    try:
        with Camoufox(
            os='macos',
            headless=False,
            user_data_dir=str(profile_dir)
        ) as browser:
            print("✅ Браузер с пользовательской директорией создан")
            
            page = browser.new_page()
            
            # Переходим на сайт и устанавливаем cookies
            page.goto("https://httpbin.org/cookies/set/persistent_test/profile_data")
            time.sleep(2)
            
            # Сохраняем локальные данные
            page.evaluate("""
                localStorage.setItem('test_profile_data', JSON.stringify({
                    profile_id: 'test_001',
                    created: new Date().toISOString(),
                    preferences: {
                        theme: 'dark',
                        language: 'ru'
                    }
                }));
            """)
            
            print("✅ Данные профиля сохранены")
            
    except Exception as e:
        print(f"❌ Ошибка при тестировании профиля: {e}")

if __name__ == "__main__":
    explore_camoufox_options()
    test_profile_persistence() 