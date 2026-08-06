#!/usr/bin/env python3
"""
Скрипт для переноса данных из demo_data в data
"""

import asyncio
import sys
from pathlib import Path

# Добавляем путь к модулям
sys.path.append(str(Path(__file__).parent))

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager
from loguru import logger


async def migrate_data():
    """Перенос данных из demo_data в data"""
    
    logger.info("🔄 Начинаем перенос данных из demo_data в data")
    
    # Инициализируем источник (demo_data)
    source_storage = StorageManager("demo_data/profiles.db")
    await source_storage.initialize()
    source_manager = ProfileManager(source_storage, "demo_data")
    await source_manager.initialize()
    
    # Инициализируем целевое место (data)
    target_storage = StorageManager("data/profiles.db")
    await target_storage.initialize()
    target_manager = ProfileManager(target_storage, "data")
    await target_manager.initialize()
    
    try:
        # Получаем все профили из demo_data
        logger.info("📋 Получаем профили из demo_data...")
        source_profiles = await source_manager.list_profiles()
        logger.info(f"Найдено {len(source_profiles)} профилей")
        
        # Получаем все группы из demo_data
        logger.info("📋 Получаем группы из demo_data...")
        source_groups = await source_storage.list_profile_groups()
        logger.info(f"Найдено {len(source_groups)} групп")
        
        # Переносим группы
        logger.info("🔄 Переносим группы...")
        for group in source_groups:
            await target_storage.save_profile_group(group)
            logger.info(f"✅ Группа перенесена: {group.name}")
        
        # Переносим профили
        logger.info("🔄 Переносим профили...")
        for profile in source_profiles:
            await target_storage.save_profile(profile)
            logger.info(f"✅ Профиль перенесен: {profile.name} ({profile.id})")
        
        # Проверяем результат
        target_profiles = await target_manager.list_profiles()
        target_groups = await target_storage.list_profile_groups()
        
        logger.success(f"🎉 Перенос завершен!")
        logger.info(f"   Профилей: {len(target_profiles)}")
        logger.info(f"   Групп: {len(target_groups)}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка переноса: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        await source_storage.close()
        await target_storage.close()


if __name__ == "__main__":
    asyncio.run(migrate_data()) 