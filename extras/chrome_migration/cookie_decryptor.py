"""
Decrypt encrypted Chrome cookies.
Supports macOS (Keychain), Windows (DPAPI) and Linux.
"""

import base64
import os
import platform
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("pycryptodome is not installed; cookie decryption is limited.")


class ChromeCookieDecryptor:
    """Decrypt Chrome cookies across platforms."""

    def __init__(self):
        self.system = platform.system().lower()
        self.encryption_key = None

    def get_chrome_encryption_key(self, chrome_data_path: str) -> bytes | None:
        """Return the Chrome encryption key for the current OS."""
        try:
            if self.system == "darwin":  # macOS
                return self._get_macos_encryption_key(chrome_data_path)
            elif self.system == "windows":
                return self._get_windows_encryption_key(chrome_data_path)
            elif self.system == "linux":
                return self._get_linux_encryption_key(chrome_data_path)
            else:
                logger.error(f"Unsupported OS: {self.system}")
                return None
        except Exception as e:
            logger.error(f"Failed to get encryption key: {e}")
            return None

    def _get_macos_encryption_key(self, chrome_data_path: str) -> bytes | None:
        """Get the Chrome encryption key from the macOS Keychain."""
        try:
            # Command to read the password from the Keychain
            cmd = [
                "security",
                "find-generic-password",
                "-w",  # password only
                "-s",
                "Chrome Safe Storage",  # service
                "-a",
                "Chrome",  # account
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            password = result.stdout.strip()

            if not password:
                logger.warning("Failed to read the password from the Keychain")
                return None

            # Decode the base64 password
            password_bytes = base64.b64decode(password)

            # PBKDF2 salt (standard for Chrome on macOS)
            salt = b"saltysalt"
            iterations = 1003

            # Derive the key with PBKDF2
            if CRYPTO_AVAILABLE:
                key = PBKDF2(password_bytes, salt, 16, iterations)
                logger.info("Chrome encryption key obtained from the Keychain")
                return key
            else:
                logger.error("pycryptodome is not installed for decryption")
                return None

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to get the key from the Keychain: {e}")
            # Try the fallback method
            return self._get_macos_fallback_key()
        except Exception as e:
            logger.error(f"Failed to get the macOS key: {e}")
            return None

    def _get_macos_fallback_key(self) -> bytes | None:
        """Fallback method to obtain the key on macOS."""
        try:
            # Default Chrome password on macOS
            password = "peanuts"
            salt = b"saltysalt"
            iterations = 1003

            if CRYPTO_AVAILABLE:
                key = PBKDF2(password.encode(), salt, 16, iterations)
                logger.info("Using the default Chrome key")
                return key
            else:
                return None
        except Exception as e:
            logger.error(f"Failed to get the fallback key: {e}")
            return None

    def _get_windows_encryption_key(self, chrome_data_path: str) -> bytes | None:
        """Get the Chrome encryption key on Windows."""
        try:
            import json

            import win32crypt

            # Read the Local State file
            local_state_path = Path(chrome_data_path) / "Local State"
            if not local_state_path.exists():
                logger.error("Local State file not found")
                return None

            with open(local_state_path, encoding="utf-8") as f:
                local_state = json.load(f)

            # Read the encrypted key
            encrypted_key = local_state.get("os_crypt", {}).get("encrypted_key")
            if not encrypted_key:
                logger.error("Encrypted key not found in Local State")
                return None

            # Decode and decrypt the key
            encrypted_key = base64.b64decode(encrypted_key)[5:]  # strip the DPAPI prefix
            key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]

            logger.info("Chrome encryption key obtained (Windows)")
            return key

        except ImportError:
            logger.error("win32crypt is unavailable. Install pywin32")
            return None
        except Exception as e:
            logger.error(f"Failed to get the Windows key: {e}")
            return None

    def _get_linux_encryption_key(self, chrome_data_path: str) -> bytes | None:
        """Get the Chrome encryption key on Linux."""
        try:
            # On Linux, Chrome uses a default password
            password = "peanuts"
            salt = b"saltysalt"
            iterations = 1

            if CRYPTO_AVAILABLE:
                key = PBKDF2(password.encode(), salt, 16, iterations)
                logger.info("Chrome encryption key obtained (Linux)")
                return key
            else:
                logger.error("pycryptodome is not installed for decryption")
                return None

        except Exception as e:
            logger.error(f"Failed to get the Linux key: {e}")
            return None

    def decrypt_chrome_cookie_value(
        self, encrypted_value: bytes, encryption_key: bytes
    ) -> str | None:
        """Decrypt a Chrome cookie value."""
        try:
            if not CRYPTO_AVAILABLE:
                logger.error("pycryptodome is not installed")
                return None

            if not encrypted_value or len(encrypted_value) < 16:
                return None

            # Chrome uses AES-128 in CBC mode
            # The first 3 bytes are the version (usually v10 or v11)
            version = encrypted_value[:3]

            if version == b"v10":
                # Old format (before Chrome 80)
                iv = b" " * 16  # empty IV
                encrypted_data = encrypted_value[3:]
            elif version == b"v11":
                # New format (Chrome 80+)
                iv = encrypted_value[3:15]  # 12-byte IV
                encrypted_data = encrypted_value[15:]
            else:
                logger.debug(f"Unknown encryption version: {version}")
                return None

            # Decrypt
            cipher = AES.new(encryption_key, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted_data)

            # Remove padding
            padding_length = decrypted[-1]
            if padding_length <= 16:
                decrypted = decrypted[:-padding_length]

            return decrypted.decode("utf-8", errors="ignore")

        except Exception as e:
            logger.debug(f"Failed to decrypt cookie: {e}")
            return None

    def get_decrypted_chrome_cookies(self, chrome_profile_path: str) -> list[dict[str, Any]]:
        """Return decrypted Chrome cookies."""
        try:
            # Get the encryption key
            chrome_data_path = str(Path(chrome_profile_path).parent)
            encryption_key = self.get_chrome_encryption_key(chrome_data_path)

            if not encryption_key:
                logger.warning("Could not obtain the encryption key; skipping encrypted cookies.")
                return self._get_unencrypted_cookies(chrome_profile_path)

            # Path to the cookie database
            cookies_path = Path(chrome_profile_path) / "Network" / "Cookies"
            if not cookies_path.exists():
                cookies_path = Path(chrome_profile_path) / "Cookies"

            if not cookies_path.exists():
                logger.error(f"Cookie database not found: {cookies_path}")
                return []

            # Make a temporary copy
            import shutil
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
                temp_path = temp_file.name

            shutil.copy2(cookies_path, temp_path)

            try:
                return self._extract_and_decrypt_cookies(temp_path, encryption_key)
            finally:
                os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Failed to get decrypted cookies: {e}")
            return []

    def _extract_and_decrypt_cookies(
        self, db_path: str, encryption_key: bytes
    ) -> list[dict[str, Any]]:
        """Extract and decrypt cookies from the database."""
        cookies = []

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Read all cookies
            cursor.execute("""
                SELECT name, value, encrypted_value, host_key, path,
                       expires_utc, is_secure, is_httponly, samesite,
                       creation_utc, last_access_utc
                FROM cookies
            """)

            rows = cursor.fetchall()

            for row in rows:
                (
                    name,
                    value,
                    encrypted_value,
                    host_key,
                    path,
                    expires_utc,
                    is_secure,
                    is_httponly,
                    samesite,
                    creation_utc,
                    last_access_utc,
                ) = row

                # Skip cookies without a name or host
                if not name or not host_key:
                    continue

                # Use the unencrypted value if present
                if value:
                    cookie_value = value
                # Otherwise try to decrypt
                elif encrypted_value:
                    cookie_value = self.decrypt_chrome_cookie_value(encrypted_value, encryption_key)
                    if not cookie_value:
                        logger.debug(f"Failed to decrypt cookie: {name}")
                        continue
                else:
                    continue

                cookie = {
                    "name": name,
                    "value": cookie_value,
                    "host_key": host_key,
                    "path": path or "/",
                    "expires_utc": expires_utc,
                    "is_secure": bool(is_secure),
                    "is_httponly": bool(is_httponly),
                    "samesite": samesite or 0,
                    "creation_utc": creation_utc,
                    "last_access_utc": last_access_utc,
                }

                cookies.append(cookie)

            conn.close()

            logger.info(f"Successfully decrypted {len(cookies)} Chrome cookies")
            return cookies

        except Exception as e:
            logger.error(f"Failed to extract cookies: {e}")
            return []

    def _get_unencrypted_cookies(self, chrome_profile_path: str) -> list[dict[str, Any]]:
        """Return only unencrypted Chrome cookies."""
        cookies = []

        try:
            cookies_path = Path(chrome_profile_path) / "Network" / "Cookies"
            if not cookies_path.exists():
                cookies_path = Path(chrome_profile_path) / "Cookies"

            if not cookies_path.exists():
                return []

            import shutil
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
                temp_path = temp_file.name

            shutil.copy2(cookies_path, temp_path)

            try:
                conn = sqlite3.connect(temp_path)
                cursor = conn.cursor()

                # Read only cookies with unencrypted values
                cursor.execute("""
                    SELECT name, value, host_key, path, expires_utc,
                           is_secure, is_httponly, samesite, creation_utc, last_access_utc
                    FROM cookies
                    WHERE value IS NOT NULL AND value != ''
                """)

                rows = cursor.fetchall()

                for row in rows:
                    (
                        name,
                        value,
                        host_key,
                        path,
                        expires_utc,
                        is_secure,
                        is_httponly,
                        samesite,
                        creation_utc,
                        last_access_utc,
                    ) = row

                    if not name or not host_key:
                        continue

                    cookie = {
                        "name": name,
                        "value": value,
                        "host_key": host_key,
                        "path": path or "/",
                        "expires_utc": expires_utc,
                        "is_secure": bool(is_secure),
                        "is_httponly": bool(is_httponly),
                        "samesite": samesite or 0,
                        "creation_utc": creation_utc,
                        "last_access_utc": last_access_utc,
                    }

                    cookies.append(cookie)

                conn.close()

                logger.info(f"Read {len(cookies)} unencrypted cookies")
                return cookies

            finally:
                os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Failed to get unencrypted cookies: {e}")
            return []
