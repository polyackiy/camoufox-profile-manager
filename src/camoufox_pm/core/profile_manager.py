"""Browser profile manager."""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loguru import logger

from .browser_session import BrowserSessionManager
from .database import StorageManager
from .fingerprint_generator import FingerprintGenerator
from .models import Profile, ProfileGroup, ProfileStatus, UsageStats


class ProfileManager:
    """Основной класс для управления профилями браузера"""
    
    def __init__(self, storage_manager: StorageManager, data_dir: str = "data"):
        self.storage = storage_manager
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "profiles"
        self.fingerprint_generator = FingerprintGenerator()

        # Active browser sessions are owned by a dedicated manager.
        self.browser_sessions = BrowserSessionManager()

        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Initialized ProfileManager with data directory: %s" % self.data_dir)
    
    async def initialize(self):
        """Инициализация менеджера профилей и базы данных"""
        await self.storage.initialize()
        logger.info("ProfileManager инициализирован")

    async def create_profile(self, 
                           name: str, 
                           group: Optional[str] = None,
                           browser_settings: Optional[Union[Dict[str, Any], 'BrowserSettings']] = None,
                           proxy_config: Optional[Dict[str, Any]] = None,
                           generate_fingerprint: bool = True) -> Profile:
        """Создать новый профиль"""
        logger.info(f"Создание нового профиля: {name}")
        
        # Создаем базовый профиль
        profile = Profile(
            name=name,
            group=group,
            notes=f"Профиль создан {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Генерируем отпечаток браузера если требуется
        if generate_fingerprint:
            fingerprint = await self.fingerprint_generator.generate_fingerprint(browser_settings)
            profile.browser_settings = fingerprint
            
        # Применяем пользовательские настройки браузера
        if browser_settings:
            # Если browser_settings - это объект BrowserSettings, используем его напрямую
            if hasattr(browser_settings, 'dict'):
                profile.browser_settings = browser_settings
            # Если это словарь, применяем поля
            elif isinstance(browser_settings, dict):
                for key, value in browser_settings.items():
                    if hasattr(profile.browser_settings, key):
                        setattr(profile.browser_settings, key, value)
        
        # Настраиваем прокси если указан
        if proxy_config:
            from .models import ProxyConfig
            profile.proxy = ProxyConfig(**proxy_config)
        
        # Создаем директорию для данных профиля
        profile_dir = Path(profile.get_storage_path(str(self.profiles_dir)))
        profile_dir.mkdir(parents=True, exist_ok=True)
        
        # Сохраняем профиль в базе данных
        await self.storage.save_profile(profile)
        
        # Логируем создание профиля
        await self.storage.log_usage(UsageStats(
            profile_id=profile.id,
            action="create_profile",
            details={"name": name, "group": group}
        ))
        
        logger.success(f"Профиль '{name}' создан с ID: {profile.id}")
        return profile

    async def get_profile(self, profile_id: str) -> Optional[Profile]:
        """Получить профиль по ID"""
        profile = await self.storage.get_profile(profile_id)
        if profile:
            logger.debug(f"Получен профиль: {profile.name} ({profile_id})")
        else:
            logger.warning(f"Профиль с ID {profile_id} не найден")
        return profile

    async def update_profile(self, profile_id: str, updates: Dict[str, Any]) -> Optional[Profile]:
        """Обновить профиль"""
        logger.info(f"Обновление профиля {profile_id}")
        
        profile = await self.get_profile(profile_id)
        if not profile:
            return None
        
        # Обновляем поля профиля
        for key, value in updates.items():
            # Маппинг proxy_config -> proxy для совместимости с API
            if key == "proxy_config":
                key = "proxy"
            
            if hasattr(profile, key):
                # Специальная обработка для status - преобразуем строку в enum
                if key == "status" and isinstance(value, str):
                    value = ProfileStatus(value)
                # Специальная обработка для proxy - преобразуем в ProxyConfig
                elif key == "proxy" and value is not None:
                    if isinstance(value, dict):
                        from camoufox_pm.core.models import ProxyConfig
                        value = ProxyConfig(**value)
                elif key == "proxy" and value is None:
                    value = None
                # Специальная обработка для browser_settings
                elif key == "browser_settings" and value is not None:
                    if isinstance(value, dict):
                        from camoufox_pm.core.models import BrowserSettings, WebRTCMode
                        # Преобразуем webrtc_mode в enum если это строка
                        if 'webrtc_mode' in value and isinstance(value['webrtc_mode'], str):
                            try:
                                value['webrtc_mode'] = WebRTCMode(value['webrtc_mode'])
                            except ValueError:
                                value['webrtc_mode'] = WebRTCMode.REPLACE
                        value = BrowserSettings(**value)
                setattr(profile, key, value)
        
        profile.updated_at = datetime.now()
        
        # Сохраняем обновления
        await self.storage.update_profile(profile)
        
        # Логируем обновление
        await self.storage.log_usage(UsageStats(
            profile_id=profile_id,
            action="update_profile", 
            details={"updated_fields": list(updates.keys())}
        ))
        
        logger.success(f"Профиль {profile_id} обновлен")
        return profile

    async def delete_profile(self, profile_id: str, remove_data: bool = True) -> bool:
        """Удалить профиль"""
        logger.info(f"Удаление профиля {profile_id}")
        
        profile = await self.get_profile(profile_id)
        if not profile:
            return False
        
        # Удаляем данные профиля с диска если требуется
        if remove_data and profile.storage_path:
            profile_path = Path(profile.storage_path)
            if profile_path.exists():
                shutil.rmtree(profile_path)
                logger.debug(f"Удалена директория профиля: {profile_path}")
        
        # Удаляем из базы данных
        success = await self.storage.delete_profile(profile_id)
        
        if success:
            # Логируем удаление
            await self.storage.log_usage(UsageStats(
                profile_id=profile_id,
                action="delete_profile",
                details={"name": profile.name, "data_removed": remove_data}
            ))
            logger.success(f"Профиль {profile_id} удален")
        else:
            logger.error(f"Ошибка при удалении профиля {profile_id}")
            
        return success

    async def list_profiles(self, 
                          group: Optional[str] = None,
                          status: Optional[ProfileStatus] = None,
                          filters: Optional[Dict[str, Any]] = None,
                          limit: Optional[int] = None,
                          offset: int = 0) -> List[Profile]:
        """Получить список профилей с фильтрацией"""
        # Объединяем старые параметры с новыми filters
        combined_filters = {}
        if group:
            combined_filters["group"] = group
        if status:
            combined_filters["status"] = status
        if filters:
            combined_filters.update(filters)
            
        profiles = await self.storage.list_profiles(combined_filters, limit, offset)
        logger.debug(f"Получено {len(profiles)} профилей")
        return profiles

    async def clone_profile(self, source_id: str, new_name: str, 
                          regenerate_fingerprint: bool = True) -> Optional[Profile]:
        """Клонировать профиль"""
        logger.info(f"Клонирование профиля {source_id} -> {new_name}")
        
        source_profile = await self.get_profile(source_id)
        if not source_profile:
            return None
        
        # Создаем копию профиля
        profile_data = source_profile.dict()
        profile_data.pop("id")  # Удаляем ID для создания нового
        profile_data["name"] = new_name
        profile_data["created_at"] = datetime.now()
        profile_data["updated_at"] = datetime.now()
        profile_data["last_used"] = None
        profile_data["storage_path"] = None
        
        new_profile = Profile(**profile_data)
        
        # Генерируем новый отпечаток если требуется
        if regenerate_fingerprint:
            fingerprint = await self.fingerprint_generator.generate_fingerprint()
            new_profile.browser_settings = fingerprint
        
        # Создаем директорию для нового профиля
        profile_dir = Path(new_profile.get_storage_path(str(self.profiles_dir)))
        profile_dir.mkdir(parents=True, exist_ok=True)
        
        # Копируем данные исходного профиля если они существуют
        source_path = Path(source_profile.get_storage_path(str(self.profiles_dir)))
        if source_path.exists():
            try:
                shutil.copytree(source_path, profile_dir, dirs_exist_ok=True)
                logger.debug(f"Скопированы данные профиля из {source_path} в {profile_dir}")
            except Exception as e:
                logger.warning(f"Не удалось скопировать данные профиля: {e}")
        
        # Сохраняем новый профиль
        await self.storage.save_profile(new_profile)
        
        # Логируем клонирование
        await self.storage.log_usage(UsageStats(
            profile_id=new_profile.id,
            action="clone_profile",
            details={"source_id": source_id, "new_name": new_name}
        ))
        
        logger.success(f"Профиль клонирован: {new_profile.id}")
        return new_profile

    async def rotate_profile_fingerprint(self, profile_id: str) -> Optional[Profile]:
        """Сменить отпечаток профиля"""
        logger.info(f"Ротация отпечатка для профиля {profile_id}")
        
        profile = await self.get_profile(profile_id)
        if not profile:
            return None
        
        # Генерируем новый отпечаток
        new_fingerprint = await self.fingerprint_generator.generate_fingerprint()
        profile.browser_settings = new_fingerprint
        profile.updated_at = datetime.now()
        
        # Сохраняем изменения
        await self.storage.update_profile(profile)
        
        # Логируем ротацию
        await self.storage.log_usage(UsageStats(
            profile_id=profile_id,
            action="rotate_fingerprint",
            details={"new_ua": new_fingerprint.user_agent[:50] + "..." if new_fingerprint.user_agent else None}
        ))
        
        logger.success(f"Отпечаток профиля {profile_id} обновлен")
        return profile

    async def export_profile(self, profile_id: str) -> Optional[bytes]:
        """Экспортировать профиль в JSON"""
        profile = await self.get_profile(profile_id)
        if not profile:
            return None
        
        # Конвертируем профиль с корректной сериализацией datetime
        profile_dict = profile.dict()
        for key, value in profile_dict.items():
            if isinstance(value, datetime):
                profile_dict[key] = value.isoformat()
        
        export_data = {
            "profile": profile_dict,
            "exported_at": datetime.now().isoformat(),
            "version": "1.0"
        }
        
        logger.info(f"Экспорт профиля {profile_id}")
        return json.dumps(export_data, indent=2, ensure_ascii=False).encode('utf-8')

    async def import_profile(self, data: bytes, new_name: Optional[str] = None) -> Optional[Profile]:
        """Импортировать профиль из JSON"""
        try:
            import_data = json.loads(data.decode('utf-8'))
            profile_data = import_data["profile"]
            
            # Создаем новый ID для импортированного профиля
            profile_data.pop("id", None)
            if new_name:
                profile_data["name"] = new_name
            
            profile = Profile(**profile_data)
            
            # Создаем директорию
            profile_dir = Path(profile.get_storage_path(str(self.profiles_dir)))
            profile_dir.mkdir(parents=True, exist_ok=True)
            
            # Сохраняем профиль
            await self.storage.save_profile(profile)
            
            logger.success(f"Профиль импортирован: {profile.id}")
            return profile
            
        except Exception as e:
            logger.error(f"Ошибка импорта профиля: {e}")
            return None

    async def get_profile_stats(self, profile_id: str) -> Dict[str, Any]:
        """Получить статистику профиля"""
        profile = await self.get_profile(profile_id)
        if not profile:
            return {}
        
        stats = await self.storage.get_profile_usage_stats(profile_id)
        
        return {
            "profile_id": profile_id,
            "name": profile.name,
            "status": profile.status,
            "created_at": profile.created_at,
            "last_used": profile.last_used,
            "total_sessions": len([s for s in stats if s.action == "launch_browser"]),
            "total_usage_time": sum([s.duration or 0 for s in stats]),
            "success_rate": len([s for s in stats if s.success]) / len(stats) if stats else 0
        }

    async def bulk_update_profiles(self, profile_ids: List[str], updates: Dict[str, Any]) -> int:
        """Массовое обновление профилей"""
        logger.info(f"Массовое обновление {len(profile_ids)} профилей")
        
        updated_count = 0
        for profile_id in profile_ids:
            try:
                result = await self.update_profile(profile_id, updates)
                if result:
                    updated_count += 1
            except Exception as e:
                logger.error(f"Ошибка обновления профиля {profile_id}: {e}")
        
        logger.success(f"Обновлено {updated_count} из {len(profile_ids)} профилей")
        return updated_count

    # Методы для работы с группами профилей
    
    async def create_group(self, name: str, description: Optional[str] = None) -> Dict[str, Any]:
        """Создать новую группу профилей"""
        logger.info(f"Создание новой группы: {name}")
        
        group = ProfileGroup(
            name=name,
            description=description
        )
        
        await self.storage.save_profile_group(group)
        
        logger.success(f"Группа '{name}' создана с ID: {group.id}")
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "created_at": group.created_at
        }
    
    async def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        """Получить группу по ID"""
        groups = await self.storage.list_profile_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if not group:
            return None
        
        # Получаем количество профилей в группе
        profiles = await self.list_profiles(filters={"group": group_id})
        
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "profile_count": len(profiles),
            "created_at": group.created_at
        }
    
    async def list_groups(self) -> List[Dict[str, Any]]:
        """Получить список всех групп"""
        groups = await self.storage.list_profile_groups()
        
        # Добавляем количество профилей для каждой группы
        result = []
        for group in groups:
            profiles = await self.list_profiles(filters={"group": group.id})
            result.append({
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "profile_count": len(profiles),
                "created_at": group.created_at
            })
        
        return result
    
    async def update_group(self, group_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Обновить группу"""
        logger.info(f"Обновление группы {group_id}")
        
        groups = await self.storage.list_profile_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if not group:
            return None
        
        # Обновляем поля группы
        for key, value in updates.items():
            if hasattr(group, key):
                setattr(group, key, value)
        
        await self.storage.save_profile_group(group)
        
        # Получаем количество профилей в группе
        profiles = await self.list_profiles(filters={"group": group_id})
        
        logger.success(f"Группа {group_id} обновлена")
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "profile_count": len(profiles),
            "created_at": group.created_at
        }
    
    async def delete_group(self, group_id: str) -> bool:
        """Удалить группу"""
        logger.info(f"Удаление группы {group_id}")
        
        # Проверяем существование группы
        groups = await self.storage.list_profile_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if not group:
            return False
        
        # Удаляем группу из профилей (делаем их без группы)
        profiles = await self.list_profiles(filters={"group": group_id})
        for profile in profiles:
            await self.update_profile(profile.id, {"group": None})
        
        # Удаляем группу
        success = await self.storage.delete_profile_group(group_id)
        
        if success:
            logger.success(f"Группа {group_id} удалена")
        else:
            logger.error(f"Ошибка при удалении группы {group_id}")
            
        return success


    # -- Browser control (delegated to BrowserSessionManager) ---------------

    async def launch_browser(
        self,
        profile_id: str,
        headless: bool = False,
        window_size: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Launch a Camoufox browser for a profile."""
        profile = await self.get_profile(profile_id)
        if not profile:
            raise ValueError(f"Profile with ID {profile_id} not found")

        if self.browser_sessions.is_running(profile_id):
            session = self.browser_sessions.active_sessions[profile_id]
            return {
                "status": "already_running",
                "profile_id": profile_id,
                "message": "Browser is already running for this profile",
                "process_id": session.process_id,
            }

        await self.update_profile(profile_id, {"last_used": datetime.now()})

        options = profile.to_camoufox_launch_options()
        options["headless"] = headless
        if window_size:
            options["window_size"] = window_size
        options.update(kwargs)

        await self.storage.log_usage(
            UsageStats(profile_id=profile_id, action="launch_browser", details={"headless": headless})
        )

        session = await self.browser_sessions.launch(profile_id, options)
        return {
            "status": "launched",
            "profile_id": profile_id,
            "message": "Browser launched successfully",
            "process_id": session.process_id,
            "camoufox_options": {"process_id": session.process_id, "options": options},
        }

    async def close_browser(self, profile_id: str) -> Dict[str, Any]:
        """Close the browser running for a profile."""
        closed = await self.browser_sessions.close(profile_id)
        if not closed:
            return {
                "status": "not_running",
                "profile_id": profile_id,
                "message": "Browser is not running for this profile",
            }
        await self.storage.log_usage(
            UsageStats(profile_id=profile_id, action="close_browser", details={"forced": True})
        )
        return {"status": "closed", "profile_id": profile_id, "message": "Browser closed successfully"}

    async def get_active_browsers(self) -> List[Dict[str, Any]]:
        """Return summaries of the browsers currently running."""
        return self.browser_sessions.list_active()

    async def close_all_browsers(self) -> Dict[str, Any]:
        """Close every active browser."""
        count = await self.browser_sessions.close_all()
        return {
            "status": "completed",
            "closed_count": count,
            "errors": [],
            "message": f"Closed {count} browsers",
        }
