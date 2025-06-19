"""
Модуль для расшифровки зашифрованных куков Chrome
Поддерживает macOS (Keychain), Windows (DPAPI) и Linux
"""

import os
import sqlite3
import platform
import subprocess
import base64
from pathlib import Path
from typing import Optional, List, Dict, Any
from loguru import logger

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("pycryptodome не установлен. Расшифровка куков будет ограничена.")


class ChromeCookieDecryptor:
    """Класс для расшифровки куков Chrome на разных платформах"""
    
    def __init__(self):
        self.system = platform.system().lower()
        self.encryption_key = None
        
    def get_chrome_encryption_key(self, chrome_data_path: str) -> Optional[bytes]:
        """Получить ключ шифрования Chrome для текущей ОС"""
        try:
            if self.system == "darwin":  # macOS
                return self._get_macos_encryption_key(chrome_data_path)
            elif self.system == "windows":
                return self._get_windows_encryption_key(chrome_data_path)
            elif self.system == "linux":
                return self._get_linux_encryption_key(chrome_data_path)
            else:
                logger.error(f"Неподдерживаемая ОС: {self.system}")
                return None
        except Exception as e:
            logger.error(f"Ошибка получения ключа шифрования: {e}")
            return None
    
    def _get_macos_encryption_key(self, chrome_data_path: str) -> Optional[bytes]:
        """Получить ключ шифрования Chrome на macOS из Keychain"""
        try:
            # Команда для получения пароля из Keychain
            cmd = [
                "security", "find-generic-password",
                "-w",  # Только пароль
                "-s", "Chrome Safe Storage",  # Сервис
                "-a", "Chrome"  # Аккаунт
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            password = result.stdout.strip()
            
            if not password:
                logger.warning("Не удалось получить пароль из Keychain")
                return None
            
            # Декодируем base64 пароль
            password_bytes = base64.b64decode(password)
            
            # Соль для PBKDF2 (стандартная для Chrome на macOS)
            salt = b'saltysalt'
            iterations = 1003
            
            # Генерируем ключ с помощью PBKDF2
            if CRYPTO_AVAILABLE:
                key = PBKDF2(password_bytes, salt, 16, iterations)
                logger.success("✅ Ключ шифрования Chrome получен из Keychain")
                return key
            else:
                logger.error("pycryptodome не установлен для расшифровки")
                return None
                
        except subprocess.CalledProcessError as e:
            logger.warning(f"Не удалось получить ключ из Keychain: {e}")
            # Пробуем альтернативный метод
            return self._get_macos_fallback_key()
        except Exception as e:
            logger.error(f"Ошибка получения ключа macOS: {e}")
            return None
    
    def _get_macos_fallback_key(self) -> Optional[bytes]:
        """Запасной метод получения ключа на macOS"""
        try:
            # Стандартный пароль Chrome на macOS
            password = "peanuts"
            salt = b'saltysalt'
            iterations = 1003
            
            if CRYPTO_AVAILABLE:
                key = PBKDF2(password.encode(), salt, 16, iterations)
                logger.info("Используется стандартный ключ Chrome")
                return key
            else:
                return None
        except Exception as e:
            logger.error(f"Ошибка получения запасного ключа: {e}")
            return None
    
    def _get_windows_encryption_key(self, chrome_data_path: str) -> Optional[bytes]:
        """Получить ключ шифрования Chrome на Windows"""
        try:
            import json
            import win32crypt
            
            # Читаем Local State файл
            local_state_path = Path(chrome_data_path) / "Local State"
            if not local_state_path.exists():
                logger.error("Local State файл не найден")
                return None
            
            with open(local_state_path, 'r', encoding='utf-8') as f:
                local_state = json.load(f)
            
            # Получаем зашифрованный ключ
            encrypted_key = local_state.get('os_crypt', {}).get('encrypted_key')
            if not encrypted_key:
                logger.error("Зашифрованный ключ не найден в Local State")
                return None
            
            # Декодируем и расшифровываем ключ
            encrypted_key = base64.b64decode(encrypted_key)[5:]  # Убираем префикс DPAPI
            key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
            
            logger.success("✅ Ключ шифрования Chrome получен (Windows)")
            return key
            
        except ImportError:
            logger.error("win32crypt не доступен. Установите pywin32")
            return None
        except Exception as e:
            logger.error(f"Ошибка получения ключа Windows: {e}")
            return None
    
    def _get_linux_encryption_key(self, chrome_data_path: str) -> Optional[bytes]:
        """Получить ключ шифрования Chrome на Linux"""
        try:
            # На Linux Chrome использует стандартный пароль
            password = "peanuts"
            salt = b'saltysalt'
            iterations = 1
            
            if CRYPTO_AVAILABLE:
                key = PBKDF2(password.encode(), salt, 16, iterations)
                logger.success("✅ Ключ шифрования Chrome получен (Linux)")
                return key
            else:
                logger.error("pycryptodome не установлен для расшифровки")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка получения ключа Linux: {e}")
            return None
    
    def decrypt_chrome_cookie_value(self, encrypted_value: bytes, encryption_key: bytes) -> Optional[str]:
        """Расшифровать значение куки Chrome"""
        try:
            if not CRYPTO_AVAILABLE:
                logger.error("pycryptodome не установлен")
                return None
            
            if not encrypted_value or len(encrypted_value) < 16:
                return None
            
            # Chrome использует AES-128 в режиме CBC
            # Первые 3 байта - версия (обычно v10 или v11)
            version = encrypted_value[:3]
            
            if version == b'v10':
                # Старый формат (до Chrome 80)
                iv = b' ' * 16  # Пустой IV
                encrypted_data = encrypted_value[3:]
            elif version == b'v11':
                # Новый формат (Chrome 80+)
                iv = encrypted_value[3:15]  # 12 байт IV
                encrypted_data = encrypted_value[15:]
            else:
                logger.debug(f"Неизвестная версия шифрования: {version}")
                return None
            
            # Расшифровываем
            cipher = AES.new(encryption_key, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted_data)
            
            # Убираем padding
            padding_length = decrypted[-1]
            if padding_length <= 16:
                decrypted = decrypted[:-padding_length]
            
            return decrypted.decode('utf-8', errors='ignore')
            
        except Exception as e:
            logger.debug(f"Ошибка расшифровки куки: {e}")
            return None
    
    def get_decrypted_chrome_cookies(self, chrome_profile_path: str) -> List[Dict[str, Any]]:
        """Получить расшифрованные куки Chrome"""
        try:
            # Получаем ключ шифрования
            chrome_data_path = str(Path(chrome_profile_path).parent)
            encryption_key = self.get_chrome_encryption_key(chrome_data_path)
            
            if not encryption_key:
                logger.warning("Не удалось получить ключ шифрования. Пропускаем зашифрованные куки.")
                return self._get_unencrypted_cookies(chrome_profile_path)
            
            # Путь к базе куков
            cookies_path = Path(chrome_profile_path) / "Network" / "Cookies"
            if not cookies_path.exists():
                cookies_path = Path(chrome_profile_path) / "Cookies"
            
            if not cookies_path.exists():
                logger.error(f"База куков не найдена: {cookies_path}")
                return []
            
            # Создаем временную копию
            import tempfile
            import shutil
            
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_file:
                temp_path = temp_file.name
            
            shutil.copy2(cookies_path, temp_path)
            
            try:
                return self._extract_and_decrypt_cookies(temp_path, encryption_key)
            finally:
                os.unlink(temp_path)
                
        except Exception as e:
            logger.error(f"Ошибка получения расшифрованных куков: {e}")
            return []
    
    def _extract_and_decrypt_cookies(self, db_path: str, encryption_key: bytes) -> List[Dict[str, Any]]:
        """Извлечь и расшифровать куки из базы данных"""
        cookies = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Получаем все куки
            cursor.execute("""
                SELECT name, value, encrypted_value, host_key, path, 
                       expires_utc, is_secure, is_httponly, samesite,
                       creation_utc, last_access_utc
                FROM cookies
            """)
            
            rows = cursor.fetchall()
            
            for row in rows:
                name, value, encrypted_value, host_key, path, expires_utc, \
                is_secure, is_httponly, samesite, creation_utc, last_access_utc = row
                
                # Пропускаем куки без имени или хоста
                if not name or not host_key:
                    continue
                
                # Если есть незашифрованное значение, используем его
                if value:
                    cookie_value = value
                # Иначе пытаемся расшифровать
                elif encrypted_value:
                    cookie_value = self.decrypt_chrome_cookie_value(encrypted_value, encryption_key)
                    if not cookie_value:
                        logger.debug(f"Не удалось расшифровать куку: {name}")
                        continue
                else:
                    continue
                
                cookie = {
                    'name': name,
                    'value': cookie_value,
                    'host_key': host_key,
                    'path': path or '/',
                    'expires_utc': expires_utc,
                    'is_secure': bool(is_secure),
                    'is_httponly': bool(is_httponly),
                    'samesite': samesite or 0,
                    'creation_utc': creation_utc,
                    'last_access_utc': last_access_utc
                }
                
                cookies.append(cookie)
            
            conn.close()
            
            logger.success(f"✅ Успешно расшифровано {len(cookies)} куков Chrome")
            return cookies
            
        except Exception as e:
            logger.error(f"Ошибка извлечения куков: {e}")
            return []
    
    def _get_unencrypted_cookies(self, chrome_profile_path: str) -> List[Dict[str, Any]]:
        """Получить только незашифрованные куки Chrome"""
        cookies = []
        
        try:
            cookies_path = Path(chrome_profile_path) / "Network" / "Cookies"
            if not cookies_path.exists():
                cookies_path = Path(chrome_profile_path) / "Cookies"
            
            if not cookies_path.exists():
                return []
            
            import tempfile
            import shutil
            
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as temp_file:
                temp_path = temp_file.name
            
            shutil.copy2(cookies_path, temp_path)
            
            try:
                conn = sqlite3.connect(temp_path)
                cursor = conn.cursor()
                
                # Получаем только куки с незашифрованными значениями
                cursor.execute("""
                    SELECT name, value, host_key, path, expires_utc, 
                           is_secure, is_httponly, samesite, creation_utc, last_access_utc
                    FROM cookies 
                    WHERE value IS NOT NULL AND value != ''
                """)
                
                rows = cursor.fetchall()
                
                for row in rows:
                    name, value, host_key, path, expires_utc, \
                    is_secure, is_httponly, samesite, creation_utc, last_access_utc = row
                    
                    if not name or not host_key:
                        continue
                    
                    cookie = {
                        'name': name,
                        'value': value,
                        'host_key': host_key,
                        'path': path or '/',
                        'expires_utc': expires_utc,
                        'is_secure': bool(is_secure),
                        'is_httponly': bool(is_httponly),
                        'samesite': samesite or 0,
                        'creation_utc': creation_utc,
                        'last_access_utc': last_access_utc
                    }
                    
                    cookies.append(cookie)
                
                conn.close()
                
                logger.info(f"Получено {len(cookies)} незашифрованных куков")
                return cookies
                
            finally:
                os.unlink(temp_path)
                
        except Exception as e:
            logger.error(f"Ошибка получения незашифрованных куков: {e}")
            return [] 