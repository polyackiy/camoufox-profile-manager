#!/usr/bin/env python3
"""
Простой тест Camoufox для изучения возможностей
"""

from camoufox.sync_api import Camoufox
import json

def simple_test():
    """Простой тест работы Camoufox"""
    print("=== Простой тест Camoufox ===")
    
    try:
        with Camoufox(
            os='macos',
            headless=False,
            geoip=False,  # отключаем geoip для простоты
            humanize=True
        ) as browser:
            print("✅ Браузер успешно создан")
            
            page = browser.new_page()
            print("✅ Страница создана")
            
            # Переходим на простой сайт
            page.goto("https://httpbin.org/headers")
            print("✅ Перешли на httpbin.org/headers")
            
            # Получаем информацию о заголовках
            content = page.content()
            print("Headers:", content[:500] + "..." if len(content) > 500 else content)
            
            # Тестируем navigator properties
            user_agent = page.evaluate("navigator.userAgent")
            print(f"User Agent: {user_agent}")
            
            platform = page.evaluate("navigator.platform")
            print(f"Platform: {platform}")
            
            languages = page.evaluate("navigator.languages")
            print(f"Languages: {languages}")
            
            print("✅ Тест завершен успешно")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    simple_test() 