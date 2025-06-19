"""
Менеджер миграции профилей Chrome в Camoufox
"""

import yaml
import asyncio
import fnmatch
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from loguru import logger

from .chrome_importer import ChromeProfileImporter
from .profile_manager import ProfileManager
from .models import Profile, BrowserSettings


class ChromeMigrationManager:
    """Менеджер для автоматизированной миграции профилей Chrome в Camoufox"""
    
    def __init__(self, profile_manager: ProfileManager, config_path: str = "config/chrome_migration_config.yaml"):
        self.profile_manager = profile_manager
        self.chrome_importer = ChromeProfileImporter()
        self.config_path = config_path
        self.config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """Загрузить конфигурацию миграции"""
        try:
            config_path = Path(self.config_path)
            if not config_path.exists():
                logger.warning(f"Конфигурационный файл не найден: {config_path}")
                return self._get_default_config()
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                logger.info(f"Загружена конфигурация миграции: {config_path}")
                return config
                
        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Получить конфигурацию по умолчанию"""
        return {
            "chrome_data_path": None,
            "migration_settings": {
                "include_cookies": True,
                "include_bookmarks": True,
                "include_history": False,
                "include_extensions": False,
                "include_passwords": False
            },
            "mapping_strategies": {
                "default": {
                    "action": "create_new",
                    "group": "chrome_imports",
                    "name_template": "Chrome - {display_name}",
                    "generate_fingerprint": True
                }
            },
            "profile_mapping": [],
            "security_settings": {
                "backup_chrome_data": True,
                "verify_data_integrity": True,
                "log_migration_details": True
            },
            "exclusion_filters": {
                "exclude_cookie_domains": [],
                "exclude_cookie_names": [],
                "exclude_history_domains": []
            }
        }
    
    async def discover_chrome_profiles(self, chrome_data_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Обнаружить все профили Chrome"""
        if not chrome_data_path:
            chrome_data_path = self.config.get("chrome_data_path")
        
        chrome_profiles = self.chrome_importer.find_chrome_profiles(chrome_data_path)
        
        # Обогащаем информацию о профилях
        enriched_profiles = []
        for profile in chrome_profiles:
            enriched_profile = profile.copy()
            enriched_profile["migration_status"] = "not_migrated"
            enriched_profile["suggested_mapping"] = await self._suggest_mapping(profile)
            enriched_profiles.append(enriched_profile)
        
        logger.info(f"Обнаружено {len(enriched_profiles)} профилей Chrome")
        return enriched_profiles
    
    async def _suggest_mapping(self, chrome_profile: Dict[str, Any]) -> Dict[str, Any]:
        """Предложить маппинг для профиля Chrome"""
        chrome_name = chrome_profile["name"]
        display_name = chrome_profile["display_name"]
        
        # Ищем точное совпадение в конфигурации
        for mapping in self.config.get("profile_mapping", []):
            if mapping.get("chrome_profile") == chrome_name:
                return {
                    "type": "configured",
                    "mapping": mapping
                }
            
            if mapping.get("chrome_display_name") == display_name:
                return {
                    "type": "configured",
                    "mapping": mapping
                }
            
            # Проверяем паттерны
            if mapping.get("chrome_profile_pattern"):
                if fnmatch.fnmatch(chrome_name, mapping["chrome_profile_pattern"]):
                    return {
                        "type": "pattern_match",
                        "mapping": mapping
                    }
        
        # Проверяем существующие профили Camoufox с похожими именами
        existing_profiles = await self.profile_manager.list_profiles()
        for existing_profile in existing_profiles:
            if display_name.lower() in existing_profile.name.lower() or \
               existing_profile.name.lower() in display_name.lower():
                return {
                    "type": "name_similarity",
                    "camoufox_profile_id": existing_profile.id,
                    "camoufox_profile_name": existing_profile.name,
                    "confidence": 0.8
                }
        
        # Предлагаем автоматическое создание
        return {
            "type": "auto_create",
            "suggested_name": f"Chrome - {display_name}",
            "suggested_group": self.config["mapping_strategies"]["default"]["group"]
        }
    
    async def migrate_profile(self, 
                            chrome_profile: Dict[str, Any], 
                            mapping: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Мигрировать один профиль Chrome"""
        logger.info(f"Начинаем миграцию профиля: {chrome_profile['display_name']}")
        
        migration_result = {
            "chrome_profile": chrome_profile["name"],
            "chrome_display_name": chrome_profile["display_name"],
            "success": False,
            "camoufox_profile_id": None,
            "camoufox_profile_name": None,
            "migration_details": {},
            "errors": [],
            "started_at": datetime.now().isoformat()
        }
        
        try:
            # Определяем маппинг
            if not mapping:
                suggested = await self._suggest_mapping(chrome_profile)
                if suggested["type"] == "configured":
                    mapping = suggested["mapping"]
                else:
                    # Используем автоматическое создание
                    # Создаем уникальное имя профиля
                    if chrome_profile['display_name'] and chrome_profile['display_name'] != chrome_profile['name']:
                        default_name = f"Chrome - {chrome_profile['display_name']} ({chrome_profile['name']})"
                    else:
                        default_name = f"Chrome - {chrome_profile['name']}"
                    
                    mapping = {
                        "create_new_profile": True,
                        "new_profile_name": suggested.get("suggested_name", default_name),
                        "new_profile_group": suggested.get("suggested_group", "chrome_imports"),
                        "migration_settings": self.config["migration_settings"]
                    }
            
            # Получаем или создаем целевой профиль Camoufox
            camoufox_profile = await self._get_or_create_target_profile(mapping)
            if not camoufox_profile:
                raise Exception("Не удалось получить или создать целевой профиль Camoufox")
            
            migration_result["camoufox_profile_id"] = camoufox_profile.id
            migration_result["camoufox_profile_name"] = camoufox_profile.name
            
            # Получаем настройки миграции
            migration_settings = mapping.get("migration_settings", self.config["migration_settings"])
            
            # Выполняем миграцию данных
            import_result = self.chrome_importer.migrate_chrome_profile_to_camoufox(
                chrome_profile["path"],
                camoufox_profile,
                include_cookies=migration_settings.get("include_cookies", True),
                include_bookmarks=migration_settings.get("include_bookmarks", True),
                include_history=migration_settings.get("include_history", False)
            )
            
            migration_result["migration_details"] = import_result
            migration_result["success"] = import_result["success"]
            migration_result["errors"] = import_result.get("errors", [])
            
            # Обновляем заметки профиля
            await self._update_profile_notes(camoufox_profile, chrome_profile, import_result)
            
            logger.success(f"Миграция профиля завершена: {chrome_profile['display_name']} -> {camoufox_profile.name}")
            
        except Exception as e:
            error_msg = f"Ошибка миграции профиля {chrome_profile['display_name']}: {e}"
            logger.error(error_msg)
            migration_result["errors"].append(error_msg)
        
        migration_result["completed_at"] = datetime.now().isoformat()
        return migration_result
    
    async def _get_or_create_target_profile(self, mapping: Dict[str, Any]) -> Optional[Profile]:
        """Получить или создать целевой профиль Camoufox"""
        # Если указан существующий профиль
        if mapping.get("camoufox_profile_id"):
            profile = await self.profile_manager.get_profile(mapping["camoufox_profile_id"])
            if profile:
                return profile
            else:
                logger.warning(f"Профиль {mapping['camoufox_profile_id']} не найден")
        
        # Если нужно создать новый профиль
        if mapping.get("create_new_profile", False):
            profile_name = mapping.get("new_profile_name", "Imported Chrome Profile")
            profile_group = mapping.get("new_profile_group", "chrome_imports")
            
            # Настройки браузера для нового профиля
            browser_settings = None
            generate_fingerprint = self.config["mapping_strategies"]["default"].get("generate_fingerprint", True)
            
            # Создаем профиль
            profile = await self.profile_manager.create_profile(
                name=profile_name,
                group=profile_group,
                browser_settings=browser_settings,
                generate_fingerprint=generate_fingerprint
            )
            
            return profile
        
        return None
    
    async def _update_profile_notes(self, 
                                  camoufox_profile: Profile, 
                                  chrome_profile: Dict[str, Any], 
                                  migration_result: Dict[str, Any]):
        """Обновить заметки профиля информацией о миграции"""
        migration_info = (
            f"Мигрировано из Chrome профиля: {chrome_profile['display_name']}\n"
            f"Исходный путь: {chrome_profile['path']}\n"
            f"Дата миграции: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Куки: {migration_result.get('cookies_imported', 0)}\n"
            f"Закладки: {migration_result.get('bookmarks_imported', 0)}\n"
            f"История: {migration_result.get('history_imported', 0)}\n"
        )
        
        # Добавляем к существующим заметкам
        existing_notes = camoufox_profile.notes or ""
        updated_notes = f"{existing_notes}\n\n--- МИГРАЦИЯ ИЗ CHROME ---\n{migration_info}"
        
        # Обновляем профиль
        await self.profile_manager.update_profile(
            camoufox_profile.id,
            {"notes": updated_notes}
        )
    
    async def migrate_all_profiles(self, 
                                 chrome_data_path: Optional[str] = None,
                                 dry_run: bool = False) -> Dict[str, Any]:
        """Мигрировать все профили Chrome согласно конфигурации"""
        logger.info("Начинаем массовую миграцию профилей Chrome")
        
        results = {
            "started_at": datetime.now().isoformat(),
            "chrome_profiles_found": 0,
            "profiles_migrated": 0,
            "profiles_failed": 0,
            "migration_results": [],
            "errors": [],
            "dry_run": dry_run
        }
        
        try:
            # Обнаруживаем профили Chrome
            chrome_profiles = await self.discover_chrome_profiles(chrome_data_path)
            results["chrome_profiles_found"] = len(chrome_profiles)
            
            if dry_run:
                logger.info("СУХОЙ ПРОГОН: миграция не будет выполнена")
                for chrome_profile in chrome_profiles:
                    suggested = await self._suggest_mapping(chrome_profile)
                    results["migration_results"].append({
                        "chrome_profile": chrome_profile["name"],
                        "chrome_display_name": chrome_profile["display_name"],
                        "suggested_mapping": suggested,
                        "would_migrate": True
                    })
                return results
            
            # Выполняем миграцию каждого профиля
            for chrome_profile in chrome_profiles:
                try:
                    migration_result = await self.migrate_profile(chrome_profile)
                    results["migration_results"].append(migration_result)
                    
                    if migration_result["success"]:
                        results["profiles_migrated"] += 1
                    else:
                        results["profiles_failed"] += 1
                        
                except Exception as e:
                    error_msg = f"Критическая ошибка миграции {chrome_profile['display_name']}: {e}"
                    logger.error(error_msg)
                    results["errors"].append(error_msg)
                    results["profiles_failed"] += 1
            
        except Exception as e:
            error_msg = f"Критическая ошибка массовой миграции: {e}"
            logger.error(error_msg)
            results["errors"].append(error_msg)
        
        results["completed_at"] = datetime.now().isoformat()
        
        # Логируем итоги
        logger.info(f"Миграция завершена: {results['profiles_migrated']} успешно, "
                   f"{results['profiles_failed']} с ошибками из {results['chrome_profiles_found']}")
        
        return results
    
    async def generate_mapping_template(self, 
                                      chrome_data_path: Optional[str] = None,
                                      output_path: str = "chrome_migration_mapping.yaml") -> str:
        """Генерировать шаблон маппинга на основе найденных профилей"""
        chrome_profiles = await self.discover_chrome_profiles(chrome_data_path)
        camoufox_profiles = await self.profile_manager.list_profiles()
        
        template = {
            "# Автоматически сгенерированный шаблон маппинга профилей": None,
            "# Создан": datetime.now().isoformat(),
            "chrome_data_path": chrome_data_path,
            "profile_mapping": []
        }
        
        for chrome_profile in chrome_profiles:
            # Создаем уникальное имя профиля для шаблона
            if chrome_profile['display_name'] and chrome_profile['display_name'] != chrome_profile['name']:
                suggested_name = f"Chrome - {chrome_profile['display_name']} ({chrome_profile['name']})"
            else:
                suggested_name = f"Chrome - {chrome_profile['name']}"
            
            mapping_entry = {
                f"# Chrome профиль: {chrome_profile['display_name']}": None,
                "chrome_profile": chrome_profile["name"],
                "chrome_display_name": chrome_profile["display_name"],
                "chrome_path": chrome_profile["path"],
                "# Выберите один из вариантов ниже:": None,
                "# Вариант 1: Перенос в существующий профиль": None,
                "camoufox_profile_id": "# укажите ID профиля",
                "# Вариант 2: Создание нового профиля": None,
                "create_new_profile": False,
                "new_profile_name": suggested_name,
                "new_profile_group": "chrome_imports",
                "migration_settings": {
                    "include_cookies": True,
                    "include_bookmarks": True,
                    "include_history": False
                }
            }
            template["profile_mapping"].append(mapping_entry)
        
        # Добавляем информацию о существующих профилях Camoufox
        template["# Существующие профили Camoufox для справки:"] = None
        template["existing_camoufox_profiles"] = []
        
        for profile in camoufox_profiles:
            template["existing_camoufox_profiles"].append({
                "id": profile.id,
                "name": profile.name,
                "group": profile.group,
                "status": profile.status
            })
        
        # Сохраняем шаблон
        with open(output_path, 'w', encoding='utf-8') as f:
            yaml.dump(template, f, allow_unicode=True, default_flow_style=False)
        
        logger.info(f"Шаблон маппинга сохранен: {output_path}")
        return output_path
    
    def save_config(self, config_path: Optional[str] = None):
        """Сохранить текущую конфигурацию"""
        if not config_path:
            config_path = self.config_path
        
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)
        
        logger.info(f"Конфигурация сохранена: {config_path}")
    
    async def get_migration_status(self) -> Dict[str, Any]:
        """Получить статус миграции"""
        chrome_profiles = await self.discover_chrome_profiles()
        camoufox_profiles = await self.profile_manager.list_profiles()
        
        # Анализируем, какие профили уже мигрированы
        migrated_profiles = []
        for profile in camoufox_profiles:
            if profile.notes and "МИГРАЦИЯ ИЗ CHROME" in profile.notes:
                migrated_profiles.append(profile)
        
        return {
            "chrome_profiles_found": len(chrome_profiles),
            "camoufox_profiles_total": len(camoufox_profiles),
            "migrated_profiles": len(migrated_profiles),
            "chrome_profiles": chrome_profiles,
            "migrated_profile_details": [{
                "id": p.id,
                "name": p.name,
                "group": p.group,
                "created_at": p.created_at.isoformat()
            } for p in migrated_profiles]
        } 