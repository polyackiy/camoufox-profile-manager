"""
Модуль для импорта куков и данных из профилей Google Chrome
"""

import os
import sqlite3
import json
import shutil
import platform
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from loguru import logger
import tempfile

from .models import Profile, BrowserSettings
from .chrome_cookie_decryptor import ChromeCookieDecryptor


class ChromeProfileImporter:
    """Класс для импорта данных из профилей Chrome"""
    
    def __init__(self):
        self.system = platform.system().lower()
        self.chrome_data_paths = self._get_chrome_data_paths()
        
    def _get_chrome_data_paths(self) -> Dict[str, str]:
        """Получить пути к данным Chrome для разных ОС"""
        home = Path.home()
        
        if self.system == "windows":
            return {
                "default": str(home / "AppData/Local/Google/Chrome/User Data"),
                "profiles": str(home / "AppData/Local/Google/Chrome/User Data")
            }
        elif self.system == "darwin":  # macOS
            return {
                "default": str(home / "Library/Application Support/Google/Chrome"),
                "profiles": str(home / "Library/Application Support/Google/Chrome")
            }
        elif self.system == "linux":
            return {
                "default": str(home / ".config/google-chrome"),
                "profiles": str(home / ".config/google-chrome")
            }
        else:
            return {
                "default": "",
                "profiles": ""
            }
    
    def find_chrome_profiles(self, chrome_data_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Найти все профили Chrome"""
        if chrome_data_path:
            base_path = Path(chrome_data_path)
        else:
            base_path = Path(self.chrome_data_paths["profiles"])
        
        if not base_path.exists():
            logger.warning(f"Путь к данным Chrome не найден: {base_path}")
            return []
        
        profiles = []
        
        # Ищем Default профиль
        default_path = base_path / "Default"
        if default_path.exists():
            profile_info = self._get_profile_info(default_path, "Default")
            if profile_info:
                profiles.append(profile_info)
        
        # Ищем профили Profile 1, Profile 2, etc.
        for item in base_path.iterdir():
            if item.is_dir() and item.name.startswith("Profile "):
                profile_info = self._get_profile_info(item, item.name)
                if profile_info:
                    profiles.append(profile_info)
        
        logger.info(f"Найдено {len(profiles)} профилей Chrome")
        return profiles
    
    def _get_profile_info(self, profile_path: Path, profile_name: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о профиле Chrome"""
        try:
            # Проверяем наличие файла Cookies
            cookies_path = profile_path / "Network" / "Cookies"
            if not cookies_path.exists():
                # Старый формат - прямо в профиле
                cookies_path = profile_path / "Cookies"
            
            # Получаем информацию о профиле из файла Preferences
            prefs_path = profile_path / "Preferences"
            profile_info = {
                "name": profile_name,
                "path": str(profile_path),
                "cookies_path": str(cookies_path) if cookies_path.exists() else None,
                "preferences_path": str(prefs_path) if prefs_path.exists() else None,
                "has_cookies": cookies_path.exists(),
                "display_name": profile_name
            }
            
            # Пытаемся получить отображаемое имя из Preferences
            if prefs_path.exists():
                try:
                    with open(prefs_path, 'r', encoding='utf-8') as f:
                        prefs = json.load(f)
                        if 'profile' in prefs and 'name' in prefs['profile']:
                            profile_info["display_name"] = prefs['profile']['name']
                        elif 'account_info' in prefs and len(prefs['account_info']) > 0:
                            # Получаем имя из аккаунта Google
                            for account in prefs['account_info']:
                                if 'full_name' in account:
                                    profile_info["display_name"] = account['full_name']
                                    break
                except Exception as e:
                    logger.debug(f"Не удалось прочитать preferences: {e}")
            
            return profile_info
            
        except Exception as e:
            logger.error(f"Ошибка получения информации о профиле {profile_name}: {e}")
            return None
    
    def export_chrome_cookies(self, chrome_profile_path: str, output_path: Optional[str] = None) -> Optional[str]:
        """Экспортировать куки из профиля Chrome в JSON с расшифровкой"""
        try:
            logger.info(f"🔍 Начинаем экспорт куков из Chrome профиля: {chrome_profile_path}")
            
            # Используем новый декриптор для получения расшифрованных куков
            decryptor = ChromeCookieDecryptor()
            cookies_data = decryptor.get_decrypted_chrome_cookies(chrome_profile_path)
            
            if not cookies_data:
                logger.warning("Не удалось получить куки из Chrome профиля")
                return None
            
            # Преобразуем куки в нужный формат для JSON
            cookies = []
            for cookie_data in cookies_data:
                try:
                    # Преобразуем временные метки Chrome в обычный формат
                    cookie = dict(cookie_data)
                    
                    if 'expires_utc' in cookie and cookie['expires_utc']:
                        try:
                            # Chrome хранит время в микросекундах с 1601 года
                            chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                            if isinstance(cookie['expires_utc'], (int, float)):
                                cookie_time = chrome_epoch + timedelta(microseconds=cookie['expires_utc'])
                                cookie['expires_utc'] = cookie_time.isoformat()
                        except Exception as e:
                            # Если ошибка в конверсии времени, устанавливаем время истечения в будущем
                            logger.debug(f"Ошибка конверсии времени для куки {cookie.get('name')}: {e}")
                            cookie['expires_utc'] = (datetime.now() + timedelta(days=365)).isoformat()
                    
                    cookies.append(cookie)
                    
                except Exception as e:
                    logger.debug(f"Ошибка обработки куки {cookie_data.get('name', 'unknown')}: {e}")
                    continue
            
            # Сохраняем куки в JSON
            if not output_path:
                output_path = f"chrome_cookies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            try:
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(cookies, f, indent=2, ensure_ascii=False)
                    
                logger.success(f"✅ Экспортировано {len(cookies)} куков в {output_path}")
                return output_path
                
            except Exception as e:
                logger.error(f"Ошибка сохранения JSON файла: {e}")
                return None
            
        except Exception as e:
            logger.error(f"Ошибка экспорта куков: {e}")
            return None
    
    def import_cookies_to_camoufox(self, cookies_json_path: str, camoufox_profile_path: str) -> bool:
        """Импортировать куки из JSON в профиль Camoufox"""
        try:
            # Читаем куки из JSON
            with open(cookies_json_path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            
            # Путь к базе куков Firefox/Camoufox
            firefox_cookies_path = Path(camoufox_profile_path) / "cookies.sqlite"
            
            # Создаем структуру базы куков Firefox если её нет
            if not firefox_cookies_path.exists():
                self._create_firefox_cookies_db(firefox_cookies_path)
            
            # Подключаемся к базе Firefox
            conn = sqlite3.connect(firefox_cookies_path)
            cursor = conn.cursor()
            
            imported_count = 0
            for cookie in cookies:
                try:
                    # Конвертируем куку Chrome в формат Firefox
                    firefox_cookie = self._convert_chrome_cookie_to_firefox(cookie)
                    
                    # Вставляем куку в базу Firefox
                    cursor.execute("""
                        INSERT OR REPLACE INTO moz_cookies 
                        (originAttributes, name, value, host, path, expiry, lastAccessed, creationTime, 
                         isSecure, isHttpOnly, inBrowserElement, sameSite, rawSameSite, schemeMap, isPartitionedAttributeSet)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, firefox_cookie)
                    
                    imported_count += 1
                    
                except Exception as e:
                    logger.debug(f"Ошибка импорта куки {cookie.get('name', 'unknown')}: {e}")
                    continue
            
            conn.commit()
            conn.close()
            
            logger.info(f"Импортировано {imported_count} куков в профиль Camoufox")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка импорта куков в Camoufox: {e}")
            return False
    
    def _create_firefox_cookies_db(self, db_path: Path):
        """Создать структуру базы куков Firefox/Camoufox"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Создаем таблицу куков в формате Firefox с полной совместимостью
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS moz_cookies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                originAttributes TEXT NOT NULL DEFAULT '',
                name TEXT,
                value TEXT,
                host TEXT,
                path TEXT,
                expiry INTEGER,
                lastAccessed INTEGER,
                creationTime INTEGER,
                isSecure INTEGER DEFAULT 0,
                isHttpOnly INTEGER DEFAULT 0,
                inBrowserElement INTEGER DEFAULT 0,
                sameSite INTEGER DEFAULT 0,
                rawSameSite INTEGER DEFAULT 0,
                schemeMap INTEGER DEFAULT 0,
                isPartitionedAttributeSet INTEGER DEFAULT 0
            )
        """)
        
        # Создаем индексы для производительности (как в реальном Firefox)
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_cookies_originAttributes ON moz_cookies(originAttributes)")
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_cookies_name ON moz_cookies(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_cookies_host ON moz_cookies(host)")
        
        conn.commit()
        conn.close()
        
        logger.debug("✅ Создана база куков Firefox/Camoufox с полной совместимостью")
    
    def _convert_chrome_cookie_to_firefox(self, chrome_cookie: Dict[str, Any]) -> Tuple:
        """Конвертировать куку Chrome в формат Firefox/Camoufox с полной совместимостью"""
        # Преобразуем временные метки
        current_time = int(datetime.now().timestamp() * 1000000)  # Firefox использует микросекунды
        
        # Обработка времени истечения
        expiry = 0
        if chrome_cookie.get('expires_utc'):
            try:
                if isinstance(chrome_cookie['expires_utc'], str):
                    # Если уже строка ISO формата
                    expiry_dt = datetime.fromisoformat(chrome_cookie['expires_utc'].replace('Z', '+00:00'))
                    expiry = int(expiry_dt.timestamp())
                elif isinstance(chrome_cookie['expires_utc'], (int, float)):
                    # Chrome timestamp в микросекундах с 1601 года
                    chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                    expiry_dt = chrome_epoch + timedelta(microseconds=chrome_cookie['expires_utc'])
                    expiry = int(expiry_dt.timestamp())
            except Exception as e:
                logger.debug(f"Ошибка конверсии времени истечения: {e}")
                # Устанавливаем время истечения через год
                expiry = int((datetime.now() + timedelta(days=365)).timestamp())
        
        # Обработка времени создания и последнего доступа
        creation_time = current_time
        last_access_time = current_time
        
        if chrome_cookie.get('creation_utc'):
            try:
                if isinstance(chrome_cookie['creation_utc'], (int, float)):
                    chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                    creation_dt = chrome_epoch + timedelta(microseconds=chrome_cookie['creation_utc'])
                    creation_time = int(creation_dt.timestamp() * 1000000)
            except:
                pass
        
        if chrome_cookie.get('last_access_utc'):
            try:
                if isinstance(chrome_cookie['last_access_utc'], (int, float)):
                    chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                    access_dt = chrome_epoch + timedelta(microseconds=chrome_cookie['last_access_utc'])
                    last_access_time = int(access_dt.timestamp() * 1000000)
            except:
                pass
        
        # Маппинг SameSite значений
        samesite_map = {0: 0, 1: 1, 2: 2}  # Chrome: 0=None, 1=Lax, 2=Strict
        samesite = samesite_map.get(chrome_cookie.get('samesite', 0), 0)
        
        # Обработка хоста (Firefox требует точку для доменных куков)
        host = chrome_cookie.get('host_key', '')
        if host and not host.startswith('.') and '.' in host:
            # Если это доменная кука без точки, добавляем точку
            if not any(host.startswith(prefix) for prefix in ['http://', 'https://', 'localhost']):
                host = '.' + host
        
        return (
            '',  # originAttributes (пустая строка для обычных куков)
            chrome_cookie.get('name', ''),
            chrome_cookie.get('value', ''),
            host,
            chrome_cookie.get('path', '/'),
            expiry,
            last_access_time,
            creation_time,
            1 if chrome_cookie.get('is_secure') else 0,
            1 if chrome_cookie.get('is_httponly') else 0,
            0,  # inBrowserElement
            samesite,
            samesite,  # rawSameSite
            0,  # schemeMap
            0   # isPartitionedAttributeSet
        )
    
    def migrate_chrome_profile_to_camoufox(self, 
                                         chrome_profile_path: str,
                                         camoufox_profile: Profile,
                                         include_cookies: bool = True,
                                         include_bookmarks: bool = True,
                                         include_history: bool = False) -> Dict[str, Any]:
        """Полная миграция профиля Chrome в Camoufox"""
        results = {
            "success": False,
            "cookies_imported": 0,
            "bookmarks_imported": 0,
            "history_imported": 0,
            "errors": []
        }
        
        try:
            chrome_path = Path(chrome_profile_path)
            camoufox_path = Path(camoufox_profile.get_storage_path())
            
            # Создаем директорию профиля Camoufox если её нет
            camoufox_path.mkdir(parents=True, exist_ok=True)
            
            # Импорт куков
            if include_cookies:
                try:
                    cookies_json = self.export_chrome_cookies(chrome_profile_path)
                    if cookies_json and self.import_cookies_to_camoufox(cookies_json, str(camoufox_path)):
                        results["cookies_imported"] = 1
                        # Удаляем временный файл
                        os.unlink(cookies_json)
                except Exception as e:
                    results["errors"].append(f"Ошибка импорта куков: {e}")
            
            # Импорт закладок
            if include_bookmarks:
                try:
                    bookmarks_path = chrome_path / "Bookmarks"
                    if bookmarks_path.exists():
                        # Копируем файл закладок (потом можно добавить конвертацию в формат Firefox)
                        shutil.copy2(bookmarks_path, camoufox_path / "chrome_bookmarks.json")
                        results["bookmarks_imported"] = 1
                except Exception as e:
                    results["errors"].append(f"Ошибка импорта закладок: {e}")
            
            # Импорт истории (опционально)
            if include_history:
                try:
                    history_path = chrome_path / "History"
                    if history_path.exists():
                        # Экспорт истории из Chrome SQLite в JSON
                        history_json = self._export_chrome_history(str(history_path))
                        if history_json:
                            with open(camoufox_path / "chrome_history.json", 'w') as f:
                                json.dump(history_json, f)
                            results["history_imported"] = len(history_json)
                except Exception as e:
                    results["errors"].append(f"Ошибка импорта истории: {e}")
            
            results["success"] = True
            logger.info(f"Миграция профиля завершена: {results}")
            
        except Exception as e:
            results["errors"].append(f"Общая ошибка миграции: {e}")
            logger.error(f"Ошибка миграции профиля: {e}")
        
        return results
    
    def _export_chrome_history(self, history_db_path: str) -> Optional[List[Dict]]:
        """Экспорт истории из Chrome"""
        try:
            # Создаем временную копию
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_file:
                temp_history_path = temp_file.name
            
            shutil.copy2(history_db_path, temp_history_path)
            
            conn = sqlite3.connect(temp_history_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT url, title, visit_count, last_visit_time
                FROM urls
                ORDER BY last_visit_time DESC
                LIMIT 1000
            """)
            
            history = []
            for row in cursor.fetchall():
                history.append({
                    "url": row[0],
                    "title": row[1],
                    "visit_count": row[2],
                    "last_visit_time": row[3]
                })
            
            conn.close()
            os.unlink(temp_history_path)
            
            return history
            
        except Exception as e:
            logger.error(f"Ошибка экспорта истории: {e}")
            return None


# Исправляем импорт pandas, если он отсутствует
try:
    import pandas as pd
except ImportError:
    # Создаем заглушку для Timedelta
    class TimedeltaStub:
        def __init__(self, microseconds):
            self.microseconds = microseconds
        
        def __radd__(self, other):
            if isinstance(other, datetime):
                return other + pd.Timedelta(microseconds=self.microseconds)
            return NotImplemented
    
    class PandasStub:
        @staticmethod
        def Timedelta(microseconds):
            from datetime import timedelta
            return timedelta(microseconds=microseconds)
    
    pd = PandasStub() 