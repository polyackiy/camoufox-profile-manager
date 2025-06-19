"""
Простой тест для проверки функциональности миграции Chrome
"""

import asyncio
import sys
from pathlib import Path
from loguru import logger

# Добавляем путь к модулям
sys.path.append(str(Path(__file__).parent))


async def test_chrome_detection():
    """Тест обнаружения профилей Chrome"""
    logger.info("🧪 Тест обнаружения профилей Chrome")
    
    try:
        from core.chrome_importer import ChromeProfileImporter
        
        importer = ChromeProfileImporter()
        
        # Показываем информацию о системе
        logger.info(f"Операционная система: {importer.system}")
        logger.info(f"Пути поиска Chrome: {importer.chrome_data_paths}")
        
        # Ищем профили
        profiles = importer.find_chrome_profiles()
        
        if profiles:
            logger.success(f"✅ Найдено {len(profiles)} профилей Chrome:")
            for i, profile in enumerate(profiles, 1):
                logger.info(f"  {i}. {profile['display_name']} ({profile['name']})")
                logger.info(f"     Путь: {profile['path']}")
                logger.info(f"     Куки: {'✓' if profile['has_cookies'] else '✗'}")
                
                # Проверяем существование файлов
                profile_path = Path(profile['path'])
                cookies_path = Path(profile['cookies_path']) if profile['cookies_path'] else None
                
                logger.info(f"     Директория существует: {'✓' if profile_path.exists() else '✗'}")
                if cookies_path:
                    logger.info(f"     Файл куков существует: {'✓' if cookies_path.exists() else '✗'}")
        else:
            logger.warning("❌ Профили Chrome не найдены")
            logger.info("💡 Возможные причины:")
            logger.info("   - Chrome не установлен")
            logger.info("   - Профили находятся в другом месте")
            logger.info("   - Нет прав доступа")
        
        return len(profiles) > 0
        
    except Exception as e:
        logger.error(f"❌ Ошибка теста обнаружения Chrome: {e}")
        return False


async def test_basic_migration():
    """Тест базовой функциональности миграции"""
    logger.info("🧪 Тест базовой миграции")
    
    try:
        from core.database import StorageManager
        from core.profile_manager import ProfileManager
        from core.chrome_migration_manager import ChromeMigrationManager
        
        # Инициализация
        storage = StorageManager()
        await storage.initialize()
        
        profile_manager = ProfileManager(storage)
        await profile_manager.initialize()
        
        migration_manager = ChromeMigrationManager(profile_manager)
        
        # Тест обнаружения профилей
        chrome_profiles = await migration_manager.discover_chrome_profiles()
        
        if not chrome_profiles:
            logger.warning("⚠️ Профили Chrome не найдены, пропускаем тест миграции")
            return True
        
        logger.info(f"Найдено {len(chrome_profiles)} профилей Chrome")
        
        # Тест генерации шаблона маппинга
        template_path = await migration_manager.generate_mapping_template(
            output_path="test_chrome_mapping.yaml"
        )
        logger.success(f"✅ Шаблон маппинга создан: {template_path}")
        
        # Тест статуса миграции
        status = await migration_manager.get_migration_status()
        logger.info(f"Статус миграции:")
        logger.info(f"  Chrome профилей: {status['chrome_profiles_found']}")
        logger.info(f"  Camoufox профилей: {status['camoufox_profiles_total']}")
        logger.info(f"  Уже мигрированных: {status['migrated_profiles']}")
        
        # Тест сухого прогона
        dry_run_result = await migration_manager.migrate_all_profiles(dry_run=True)
        logger.info(f"Сухой прогон: будет обработано {dry_run_result['chrome_profiles_found']} профилей")
        
        logger.success("✅ Базовый тест миграции прошел успешно")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка базового теста миграции: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def test_config_generation():
    """Тест генерации конфигурации"""
    logger.info("🧪 Тест генерации конфигурации")
    
    try:
        # Тестируем загрузку дефолтной конфигурации
        from core.chrome_migration_manager import ChromeMigrationManager
        from core.profile_manager import ProfileManager
        from core.database import StorageManager
        
        storage = StorageManager()
        await storage.initialize()
        profile_manager = ProfileManager(storage)
        await profile_manager.initialize()
        
        # Создаем менеджер с несуществующим конфигом (должен использовать дефолтный)
        migration_manager = ChromeMigrationManager(
            profile_manager, 
            config_path="nonexistent_config.yaml"
        )
        
        # Проверяем, что дефолтная конфигурация загружена
        assert migration_manager.config is not None
        assert "default_migration_settings" in migration_manager.config
        
        logger.success("✅ Дефолтная конфигурация загружена")
        
        # Тестируем сохранение конфигурации
        test_config_path = "test_chrome_config.yaml"
        migration_manager.config_path = test_config_path
        migration_manager.save_config()
        
        # Проверяем, что файл создан
        config_file = Path(test_config_path)
        if config_file.exists():
            logger.success(f"✅ Конфигурация сохранена: {test_config_path}")
            
            # Читаем и проверяем содержимое
            import yaml
            with open(test_config_path, 'r', encoding='utf-8') as f:
                saved_config = yaml.safe_load(f)
            
            assert saved_config is not None
            assert "default_migration_settings" in saved_config
            
            logger.success("✅ Конфигурация корректно сохранена и загружена")
            
            # Убираем тестовый файл
            config_file.unlink()
        else:
            logger.error("❌ Файл конфигурации не создан")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка теста конфигурации: {e}")
        return False


def test_chrome_paths():
    """Тест определения путей Chrome"""
    logger.info("🧪 Тест определения путей Chrome")
    
    try:
        from core.chrome_importer import ChromeProfileImporter
        
        importer = ChromeProfileImporter()
        
        # Проверяем, что пути определены
        assert importer.chrome_data_paths is not None
        assert "profiles" in importer.chrome_data_paths
        
        logger.info(f"Система: {importer.system}")
        logger.info(f"Путь к профилям: {importer.chrome_data_paths['profiles']}")
        
        # Проверяем, что пуь корректный для текущей ОС
        profiles_path = importer.chrome_data_paths['profiles']
        assert profiles_path != ""
        
        if importer.system == "windows":
            assert "AppData" in profiles_path
        elif importer.system == "darwin":
            assert "Library" in profiles_path
        elif importer.system == "linux":
            assert ".config" in profiles_path
        
        logger.success("✅ Пути Chrome корректно определены")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка теста путей Chrome: {e}")
        return False


async def run_all_tests():
    """Запуск всех тестов"""
    logger.info("🚀 Запуск тестов миграции Chrome")
    logger.info("=" * 50)
    
    tests = [
        ("Определение путей Chrome", test_chrome_paths),
        ("Обнаружение профилей Chrome", test_chrome_detection),
        ("Генерация конфигурации", test_config_generation),
        ("Базовая функциональность миграции", test_basic_migration),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        logger.info(f"\n🔍 Выполняется: {test_name}")
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"❌ Критическая ошибка в тесте '{test_name}': {e}")
            results.append((test_name, False))
    
    # Выводим итоги
    logger.info("\n📊 Результаты тестов:")
    logger.info("=" * 50)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        if result:
            logger.success(f"✅ {test_name}")
            passed += 1
        else:
            logger.error(f"❌ {test_name}")
    
    logger.info(f"\n📈 Итого: {passed}/{total} тестов прошли успешно")
    
    if passed == total:
        logger.success("🎉 Все тесты прошли успешно!")
        logger.info("💡 Система миграции Chrome готова к использованию")
        logger.info("📖 Запустите chrome_migration_wizard.py для интерактивной миграции")
    else:
        logger.warning(f"⚠️ {total - passed} тестов не прошли")
        logger.info("🔧 Проверьте логи выше для устранения проблем")
    
    return passed == total


if __name__ == "__main__":
    asyncio.run(run_all_tests()) 