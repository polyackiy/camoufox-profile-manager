#!/usr/bin/env python3
"""
Скрипт для проверки корректности переноса куков из Chrome в Camoufox
"""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

# Добавляем путь к модулям
sys.path.append(str(Path(__file__).parent))

from loguru import logger


def check_chrome_cookies(chrome_profile_path: str):
    """Проверить куки в Chrome профиле"""
    logger.info(f"🔍 Проверяем Chrome профиль: {chrome_profile_path}")
    
    chrome_path = Path(chrome_profile_path)
    cookies_path = chrome_path / "Network" / "Cookies"
    
    if not cookies_path.exists():
        cookies_path = chrome_path / "Cookies"
    
    if not cookies_path.exists():
        logger.error(f"❌ Файл куков Chrome не найден: {cookies_path}")
        return 0
    
    try:
        # Читаем куки из Chrome SQLite
        conn = sqlite3.connect(cookies_path)
        cursor = conn.cursor()
        
        # Получаем количество куков
        cursor.execute("SELECT COUNT(*) FROM cookies")
        count = cursor.fetchone()[0]
        
        # Получаем примеры куков
        cursor.execute("SELECT name, host_key, path, expires_utc FROM cookies LIMIT 5")
        examples = cursor.fetchall()
        
        conn.close()
        
        logger.success(f"✅ Chrome профиль содержит {count} куков")
        logger.info("Примеры куков:")
        for name, host, path, expires in examples:
            logger.info(f"  - {name} @ {host}{path} (expires: {expires})")
        
        return count
        
    except Exception as e:
        logger.error(f"❌ Ошибка чтения Chrome куков: {e}")
        return 0


def check_camoufox_cookies(camoufox_profile_path: str):
    """Проверить куки в Camoufox профиле"""
    logger.info(f"🔍 Проверяем Camoufox профиль: {camoufox_profile_path}")
    
    camoufox_path = Path(camoufox_profile_path)
    cookies_path = camoufox_path / "cookies.sqlite"
    
    if not cookies_path.exists():
        logger.error(f"❌ Файл куков Camoufox не найден: {cookies_path}")
        return 0
    
    try:
        # Читаем куки из Camoufox SQLite
        conn = sqlite3.connect(cookies_path)
        cursor = conn.cursor()
        
        # Получаем количество куков
        cursor.execute("SELECT COUNT(*) FROM moz_cookies")
        count = cursor.fetchone()[0]
        
        # Получаем примеры куков
        cursor.execute("SELECT name, host, path, expiry FROM moz_cookies LIMIT 5")
        examples = cursor.fetchall()
        
        # Проверяем структуру таблицы
        cursor.execute("PRAGMA table_info(moz_cookies)")
        columns = cursor.fetchall()
        
        conn.close()
        
        logger.success(f"✅ Camoufox профиль содержит {count} куков")
        logger.info("Примеры куков:")
        for name, host, path, expiry in examples:
            logger.info(f"  - {name} @ {host}{path} (expires: {expiry})")
        
        logger.info(f"Структура таблицы moz_cookies: {len(columns)} колонок")
        for col in columns:
            logger.info(f"  - {col[1]} ({col[2]})")
        
        return count
        
    except Exception as e:
        logger.error(f"❌ Ошибка чтения Camoufox куков: {e}")
        return 0


def check_exported_json(json_file_path: str):
    """Проверить экспортированный JSON файл куков"""
    json_path = Path(json_file_path)
    
    if not json_path.exists():
        logger.warning(f"⚠️ JSON файл не найден: {json_path}")
        return 0
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        
        logger.success(f"✅ JSON файл содержит {len(cookies)} куков")
        
        # Проверяем примеры
        for i, cookie in enumerate(cookies[:3]):
            logger.info(f"  Кука {i+1}: {cookie.get('name', 'unknown')} @ {cookie.get('host_key', 'unknown')}")
            
        return len(cookies)
        
    except Exception as e:
        logger.error(f"❌ Ошибка чтения JSON файла: {e}")
        return 0


def test_cookie_conversion():
    """Тест конверсии куки из Chrome в Firefox формат"""
    logger.info("🧪 Тестируем конверсию куков...")
    
    # Пример Chrome куки
    chrome_cookie = {
        "name": "test_cookie",
        "value": "test_value",
        "host_key": ".example.com",
        "path": "/",
        "expires_utc": "1734542400000000",  # Chrome timestamp
        "is_secure": 1,
        "is_httponly": 1,
        "samesite": 1
    }
    
    logger.info(f"Chrome кука: {chrome_cookie}")
    
    # Используем функцию из chrome_importer
    try:
        from core.chrome_importer import ChromeProfileImporter
        importer = ChromeProfileImporter()
        firefox_cookie = importer._convert_chrome_cookie_to_firefox(chrome_cookie)
        
        logger.success(f"✅ Firefox кука: {firefox_cookie}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка конверсии: {e}")
        return False


def main():
    """Главная функция проверки"""
    logger.info("🦊 Проверка переноса куков Chrome → Camoufox")
    logger.info("=" * 60)
    
    # Пути для проверки
    chrome_profile = "/Volumes/Disk2/apol/Library/Application Support/Google/Chrome/Default"
    camoufox_profile = "data/profiles/profile_6eqs93w4"
    json_files = list(Path(".").glob("chrome_cookies_*.json"))
    
    # Проверяем Chrome профиль
    chrome_count = check_chrome_cookies(chrome_profile)
    
    # Проверяем Camoufox профиль  
    camoufox_count = check_camoufox_cookies(camoufox_profile)
    
    # Проверяем JSON файлы
    json_count = 0
    if json_files:
        latest_json = sorted(json_files)[-1]  # Берем последний по времени
        logger.info(f"📄 Проверяем JSON файл: {latest_json}")
        json_count = check_exported_json(str(latest_json))
    
    # Тест конверсии
    conversion_ok = test_cookie_conversion()
    
    # Итоговый отчет
    logger.info("\n" + "=" * 60)
    logger.info("📊 ИТОГОВЫЙ ОТЧЕТ:")
    logger.info(f"Chrome профиль:     {chrome_count} куков")
    logger.info(f"JSON экспорт:       {json_count} куков")
    logger.info(f"Camoufox профиль:   {camoufox_count} куков")
    logger.info(f"Конверсия работает: {'✅' if conversion_ok else '❌'}")
    
    # Анализ
    if chrome_count > 0:
        export_efficiency = (json_count / chrome_count) * 100 if chrome_count > 0 else 0
        import_efficiency = (camoufox_count / json_count) * 100 if json_count > 0 else 0
        total_efficiency = (camoufox_count / chrome_count) * 100 if chrome_count > 0 else 0
        
        logger.info(f"\n📈 ЭФФЕКТИВНОСТЬ ПЕРЕНОСА:")
        logger.info(f"Chrome → JSON:      {export_efficiency:.1f}%")
        logger.info(f"JSON → Camoufox:    {import_efficiency:.1f}%")
        logger.info(f"Chrome → Camoufox:  {total_efficiency:.1f}%")
        
        if total_efficiency < 50:
            logger.warning("⚠️ Низкая эффективность переноса!")
            logger.info("Возможные причины:")
            logger.info("- Зашифрованные куки Chrome")
            logger.info("- Ошибки в конверсии формата") 
            logger.info("- Фильтрация недействительных куков")


if __name__ == "__main__":
    main() 