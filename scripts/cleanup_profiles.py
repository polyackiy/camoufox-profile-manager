#!/usr/bin/env python3
"""
Утилита для очистки оставшихся файлов профилей и диагностики проблем с удалением
"""

import os
import shutil
import asyncio
from pathlib import Path
from typing import List, Dict, Set
from loguru import logger

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.models import Profile


class ProfileCleanupManager:
    """Менеджер для очистки файлов профилей"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "profiles"
        self.storage = StorageManager(str(self.data_dir / "profiles.db"))
    
    async def initialize(self):
        """Инициализация"""
        await self.storage.initialize()
        logger.info("🔧 ProfileCleanupManager инициализирован")
    
    def get_profile_directories_on_disk(self) -> List[Path]:
        """Получить все директории профилей на диске"""
        if not self.profiles_dir.exists():
            return []
        
        profile_dirs = []
        for item in self.profiles_dir.iterdir():
            if item.is_dir() and item.name.startswith("profile_"):
                profile_dirs.append(item)
        
        return profile_dirs
    
    async def get_profiles_in_database(self) -> List[Profile]:
        """Получить все профили из базы данных"""
        return await self.storage.list_profiles()
    
    def extract_profile_id_from_path(self, profile_path: Path) -> str:
        """Извлечь ID профиля из пути"""
        # profile_xyz123 -> xyz123
        return profile_path.name.replace("profile_", "")
    
    async def find_orphaned_profile_directories(self) -> List[Dict[str, any]]:
        """Найти директории профилей, которых нет в базе данных"""
        logger.info("🔍 Поиск осиротевших директорий профилей...")
        
        # Получаем данные
        disk_dirs = self.get_profile_directories_on_disk()
        db_profiles = await self.get_profiles_in_database()
        
        # Создаем множество ID профилей из БД
        db_profile_ids = {profile.id for profile in db_profiles}
        
        # Находим осиротевшие директории
        orphaned = []
        for profile_dir in disk_dirs:
            profile_id = self.extract_profile_id_from_path(profile_dir)
            
            if profile_id not in db_profile_ids:
                # Получаем информацию о директории
                size = self.get_directory_size(profile_dir)
                modified = profile_dir.stat().st_mtime
                
                orphaned.append({
                    'path': profile_dir,
                    'profile_id': profile_id,
                    'size_mb': size / (1024 * 1024),
                    'modified': modified,
                    'files_count': len(list(profile_dir.rglob('*')))
                })
        
        logger.info(f"📊 Найдено {len(orphaned)} осиротевших директорий")
        return orphaned
    
    async def find_missing_profile_directories(self) -> List[Dict[str, any]]:
        """Найти профили в БД, у которых нет директорий на диске"""
        logger.info("🔍 Поиск профилей без директорий...")
        
        disk_dirs = self.get_profile_directories_on_disk()
        db_profiles = await self.get_profiles_in_database()
        
        # Создаем множество ID профилей на диске
        disk_profile_ids = {self.extract_profile_id_from_path(d) for d in disk_dirs}
        
        # Находим профили без директорий
        missing = []
        for profile in db_profiles:
            if profile.id not in disk_profile_ids:
                missing.append({
                    'profile': profile,
                    'expected_path': self.profiles_dir / f"profile_{profile.id}"
                })
        
        logger.info(f"📊 Найдено {len(missing)} профилей без директорий")
        return missing
    
    def get_directory_size(self, path: Path) -> int:
        """Получить размер директории в байтах"""
        total_size = 0
        try:
            for file_path in path.rglob('*'):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
        except (OSError, PermissionError):
            pass
        return total_size
    
    async def cleanup_orphaned_directories(self, orphaned: List[Dict], confirm: bool = True) -> int:
        """Очистить осиротевшие директории"""
        if not orphaned:
            logger.info("✅ Нет осиротевших директорий для очистки")
            return 0
        
        logger.warning(f"🗑️ Найдено {len(orphaned)} осиротевших директорий:")
        
        total_size = 0
        for item in orphaned:
            logger.warning(f"  - {item['path'].name}: {item['size_mb']:.1f} MB, {item['files_count']} файлов")
            total_size += item['size_mb']
        
        logger.warning(f"📊 Общий размер: {total_size:.1f} MB")
        
        if confirm:
            response = input(f"\n⚠️ Удалить {len(orphaned)} осиротевших директорий? (yes/no): ").lower()
            if response not in ['yes', 'y', 'да']:
                logger.info("❌ Очистка отменена")
                return 0
        
        # Удаляем директории
        deleted_count = 0
        for item in orphaned:
            try:
                shutil.rmtree(item['path'])
                logger.success(f"✅ Удалена: {item['path'].name}")
                deleted_count += 1
            except Exception as e:
                logger.error(f"❌ Ошибка удаления {item['path'].name}: {e}")
        
        logger.success(f"🎉 Очистка завершена! Удалено {deleted_count} директорий")
        return deleted_count
    
    async def create_missing_directories(self, missing: List[Dict]) -> int:
        """Создать отсутствующие директории профилей"""
        if not missing:
            logger.info("✅ Все профили имеют директории")
            return 0
        
        logger.info(f"📁 Создание {len(missing)} отсутствующих директорий...")
        
        created_count = 0
        for item in missing:
            try:
                item['expected_path'].mkdir(parents=True, exist_ok=True)
                logger.success(f"✅ Создана: {item['expected_path'].name}")
                created_count += 1
            except Exception as e:
                logger.error(f"❌ Ошибка создания {item['expected_path'].name}: {e}")
        
        logger.success(f"🎉 Создано {created_count} директорий")
        return created_count
    
    async def full_diagnostic(self) -> Dict[str, any]:
        """Полная диагностика состояния профилей"""
        logger.info("🔍 Запуск полной диагностики профилей...")
        
        # Основная статистика
        disk_dirs = self.get_profile_directories_on_disk()
        db_profiles = await self.get_profiles_in_database()
        
        # Поиск проблем
        orphaned = await self.find_orphaned_profile_directories()
        missing = await self.find_missing_profile_directories()
        
        # Подсчет размеров
        total_disk_size = sum(self.get_directory_size(d) for d in disk_dirs) / (1024 * 1024)
        orphaned_size = sum(item['size_mb'] for item in orphaned)
        
        diagnostic_result = {
            'total_profiles_in_db': len(db_profiles),
            'total_directories_on_disk': len(disk_dirs),
            'total_disk_size_mb': total_disk_size,
            'orphaned_directories': len(orphaned),
            'orphaned_size_mb': orphaned_size,
            'missing_directories': len(missing),
            'healthy_profiles': len(db_profiles) - len(missing),
            'issues_found': len(orphaned) + len(missing)
        }
        
        # Вывод результатов
        logger.info("📊 Результаты диагностики:")
        logger.info(f"  Профилей в БД: {diagnostic_result['total_profiles_in_db']}")
        logger.info(f"  Директорий на диске: {diagnostic_result['total_directories_on_disk']}")
        logger.info(f"  Общий размер: {diagnostic_result['total_disk_size_mb']:.1f} MB")
        logger.info(f"  Осиротевших директорий: {diagnostic_result['orphaned_directories']} ({diagnostic_result['orphaned_size_mb']:.1f} MB)")
        logger.info(f"  Профилей без директорий: {diagnostic_result['missing_directories']}")
        logger.info(f"  Здоровых профилей: {diagnostic_result['healthy_profiles']}")
        
        if diagnostic_result['issues_found'] == 0:
            logger.success("✅ Проблем не найдено!")
        else:
            logger.warning(f"⚠️ Найдено {diagnostic_result['issues_found']} проблем")
        
        return diagnostic_result
    
    async def auto_cleanup(self, dry_run: bool = False) -> Dict[str, int]:
        """Автоматическая очистка (удаление осиротевших, создание отсутствующих)"""
        logger.info("🤖 Запуск автоматической очистки...")
        
        if dry_run:
            logger.info("🔍 Режим тестового запуска (dry-run)")
        
        # Находим проблемы
        orphaned = await self.find_orphaned_profile_directories()
        missing = await self.find_missing_profile_directories()
        
        results = {
            'orphaned_removed': 0,
            'directories_created': 0
        }
        
        # Удаляем осиротевшие директории
        if orphaned and not dry_run:
            results['orphaned_removed'] = await self.cleanup_orphaned_directories(orphaned, confirm=False)
        elif orphaned:
            logger.info(f"🔍 Dry-run: Будет удалено {len(orphaned)} осиротевших директорий")
        
        # Создаем отсутствующие директории
        if missing and not dry_run:
            results['directories_created'] = await self.create_missing_directories(missing)
        elif missing:
            logger.info(f"🔍 Dry-run: Будет создано {len(missing)} директорий")
        
        return results
    
    async def close(self):
        """Закрытие соединений"""
        await self.storage.close()


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Утилита очистки профилей Camoufox')
    parser.add_argument('--action', choices=['diagnostic', 'cleanup', 'auto'], 
                       default='diagnostic', help='Действие для выполнения')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Тестовый запуск без изменений')
    parser.add_argument('--data-dir', default='data', 
                       help='Путь к директории данных')
    
    args = parser.parse_args()
    
    cleanup_manager = ProfileCleanupManager(args.data_dir)
    await cleanup_manager.initialize()
    
    try:
        if args.action == 'diagnostic':
            await cleanup_manager.full_diagnostic()
            
        elif args.action == 'cleanup':
            orphaned = await cleanup_manager.find_orphaned_profile_directories()
            if orphaned:
                await cleanup_manager.cleanup_orphaned_directories(orphaned)
            else:
                logger.info("✅ Нет осиротевших директорий для очистки")
                
        elif args.action == 'auto':
            results = await cleanup_manager.auto_cleanup(dry_run=args.dry_run)
            logger.info(f"🎉 Автоочистка завершена: удалено {results['orphaned_removed']}, создано {results['directories_created']}")
    
    finally:
        await cleanup_manager.close()


if __name__ == "__main__":
    asyncio.run(main()) 