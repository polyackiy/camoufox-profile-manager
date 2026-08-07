"""
Import cookies and data from Google Chrome profiles.
"""

import json
import os
import platform
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from camoufox_pm.core.models import Profile

from .cookie_decryptor import ChromeCookieDecryptor


class ChromeProfileImporter:
    """Import data from Chrome profiles."""

    def __init__(self):
        self.system = platform.system().lower()
        self.chrome_data_paths = self._get_chrome_data_paths()

    def _get_chrome_data_paths(self) -> dict[str, str]:
        """Return Chrome data paths per OS."""
        home = Path.home()

        if self.system == "windows":
            return {
                "default": str(home / "AppData/Local/Google/Chrome/User Data"),
                "profiles": str(home / "AppData/Local/Google/Chrome/User Data"),
            }
        elif self.system == "darwin":  # macOS
            return {
                "default": str(home / "Library/Application Support/Google/Chrome"),
                "profiles": str(home / "Library/Application Support/Google/Chrome"),
            }
        elif self.system == "linux":
            return {
                "default": str(home / ".config/google-chrome"),
                "profiles": str(home / ".config/google-chrome"),
            }
        else:
            return {"default": "", "profiles": ""}

    def find_chrome_profiles(self, chrome_data_path: str | None = None) -> list[dict[str, Any]]:
        """Find all Chrome profiles."""
        if chrome_data_path:
            base_path = Path(chrome_data_path)
        else:
            base_path = Path(self.chrome_data_paths["profiles"])

        if not base_path.exists():
            logger.warning(f"Chrome data path not found: {base_path}")
            return []

        profiles = []

        # Look for the Default profile
        default_path = base_path / "Default"
        if default_path.exists():
            profile_info = self._get_profile_info(default_path, "Default")
            if profile_info:
                profiles.append(profile_info)

        # Look for Profile 1, Profile 2, etc.
        for item in base_path.iterdir():
            if item.is_dir() and item.name.startswith("Profile "):
                profile_info = self._get_profile_info(item, item.name)
                if profile_info:
                    profiles.append(profile_info)

        logger.info(f"Found {len(profiles)} Chrome profiles")
        return profiles

    def _get_profile_info(self, profile_path: Path, profile_name: str) -> dict[str, Any] | None:
        """Return information about a Chrome profile."""
        try:
            # Check that the Cookies file exists
            cookies_path = profile_path / "Network" / "Cookies"
            if not cookies_path.exists():
                # Old format: directly in the profile
                cookies_path = profile_path / "Cookies"

            # Read profile info from the Preferences file
            prefs_path = profile_path / "Preferences"
            profile_info = {
                "name": profile_name,
                "path": str(profile_path),
                "cookies_path": str(cookies_path) if cookies_path.exists() else None,
                "preferences_path": str(prefs_path) if prefs_path.exists() else None,
                "has_cookies": cookies_path.exists(),
                "display_name": profile_name,
            }

            # Try to read the display name from Preferences
            if prefs_path.exists():
                try:
                    with open(prefs_path, encoding="utf-8") as f:
                        prefs = json.load(f)
                        if "profile" in prefs and "name" in prefs["profile"]:
                            profile_info["display_name"] = prefs["profile"]["name"]
                        elif "account_info" in prefs and len(prefs["account_info"]) > 0:
                            # Read the name from the Google account
                            for account in prefs["account_info"]:
                                if "full_name" in account:
                                    profile_info["display_name"] = account["full_name"]
                                    break
                except Exception as e:
                    logger.debug(f"Failed to read preferences: {e}")

            return profile_info

        except Exception as e:
            logger.error(f"Failed to get info for profile {profile_name}: {e}")
            return None

    def export_chrome_cookies(
        self, chrome_profile_path: str, output_path: str | None = None
    ) -> str | None:
        """Export and decrypt cookies from a Chrome profile to JSON."""
        try:
            logger.info(f"Exporting cookies from Chrome profile: {chrome_profile_path}")

            # Use the decryptor to get decrypted cookies
            decryptor = ChromeCookieDecryptor()
            cookies_data = decryptor.get_decrypted_chrome_cookies(chrome_profile_path)

            if not cookies_data:
                logger.warning("Failed to read cookies from the Chrome profile")
                return None

            # Convert cookies to the JSON format
            cookies = []
            for cookie_data in cookies_data:
                try:
                    # Convert Chrome timestamps to a normal format
                    cookie = dict(cookie_data)

                    if "expires_utc" in cookie and cookie["expires_utc"]:
                        try:
                            # Chrome stores time in microseconds since 1601
                            chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                            if isinstance(cookie["expires_utc"], (int, float)):
                                cookie_time = chrome_epoch + timedelta(
                                    microseconds=cookie["expires_utc"]
                                )
                                cookie["expires_utc"] = cookie_time.isoformat()
                        except Exception as e:
                            # On a time-conversion error, set the expiry in the future
                            logger.debug(
                                f"Failed to convert time for cookie {cookie.get('name')}: {e}"
                            )
                            cookie["expires_utc"] = (
                                datetime.now() + timedelta(days=365)
                            ).isoformat()

                    cookies.append(cookie)

                except Exception as e:
                    logger.debug(
                        f"Failed to process cookie {cookie_data.get('name', 'unknown')}: {e}"
                    )
                    continue

            # Save cookies to JSON
            if not output_path:
                output_path = f"chrome_cookies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, indent=2, ensure_ascii=False)

                logger.info(f"Exported {len(cookies)} cookies to {output_path}")
                return output_path

            except Exception as e:
                logger.error(f"Failed to save JSON file: {e}")
                return None

        except Exception as e:
            logger.error(f"Failed to export cookies: {e}")
            return None

    def import_cookies_to_camoufox(
        self, cookies_json_path: str, camoufox_profile_path: str
    ) -> bool:
        """Import cookies from JSON into a Camoufox profile."""
        try:
            # Read cookies from JSON
            with open(cookies_json_path, encoding="utf-8") as f:
                cookies = json.load(f)

            # Path to the cookie database Firefox/Camoufox
            firefox_cookies_path = Path(camoufox_profile_path) / "cookies.sqlite"

            # Create the Firefox cookie database schema if missing
            if not firefox_cookies_path.exists():
                self._create_firefox_cookies_db(firefox_cookies_path)

            # Connect to the Firefox database
            conn = sqlite3.connect(firefox_cookies_path)
            cursor = conn.cursor()

            imported_count = 0
            for cookie in cookies:
                try:
                    # Convert a Chrome cookie to the Firefox format
                    firefox_cookie = self._convert_chrome_cookie_to_firefox(cookie)

                    # Insert the cookie into the Firefox database
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO moz_cookies
                        (originAttributes, name, value, host, path, expiry, lastAccessed, creationTime,
                         isSecure, isHttpOnly, inBrowserElement, sameSite, rawSameSite, schemeMap, isPartitionedAttributeSet)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        firefox_cookie,
                    )

                    imported_count += 1

                except Exception as e:
                    logger.debug(f"Failed to import cookie {cookie.get('name', 'unknown')}: {e}")
                    continue

            conn.commit()
            conn.close()

            logger.info(f"Imported {imported_count} cookies into the Camoufox profile")
            return True

        except Exception as e:
            logger.error(f"Failed to import cookies into Camoufox: {e}")
            return False

    def _create_firefox_cookies_db(self, db_path: Path):
        """Create the Firefox/Camoufox cookie database schema."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create the Firefox-compatible cookie table
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

        # Create indexes for performance (as real Firefox does)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS moz_cookies_originAttributes ON moz_cookies(originAttributes)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_cookies_name ON moz_cookies(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS moz_cookies_host ON moz_cookies(host)")

        conn.commit()
        conn.close()

        logger.debug("Created the Firefox/Camoufox cookie database")

    def _convert_chrome_cookie_to_firefox(self, chrome_cookie: dict[str, Any]) -> tuple:
        """Convert a Chrome cookie to the Firefox/Camoufox format."""
        # Convert the timestamps
        current_time = int(datetime.now().timestamp() * 1000000)  # Firefox uses microseconds

        # Handle the expiry time
        expiry = 0
        if chrome_cookie.get("expires_utc"):
            try:
                if isinstance(chrome_cookie["expires_utc"], str):
                    # Already an ISO-format string
                    expiry_dt = datetime.fromisoformat(
                        chrome_cookie["expires_utc"].replace("Z", "+00:00")
                    )
                    expiry = int(expiry_dt.timestamp())
                elif isinstance(chrome_cookie["expires_utc"], (int, float)):
                    # Chrome timestamp in microseconds since 1601
                    chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                    expiry_dt = chrome_epoch + timedelta(microseconds=chrome_cookie["expires_utc"])
                    expiry = int(expiry_dt.timestamp())
            except Exception as e:
                logger.debug(f"Failed to convert the expiry time: {e}")
                # Set the expiry one year out
                expiry = int((datetime.now() + timedelta(days=365)).timestamp())

        # Handle the creation and last-access times
        creation_time = current_time
        last_access_time = current_time

        if chrome_cookie.get("creation_utc"):
            try:
                if isinstance(chrome_cookie["creation_utc"], (int, float)):
                    chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                    creation_dt = chrome_epoch + timedelta(
                        microseconds=chrome_cookie["creation_utc"]
                    )
                    creation_time = int(creation_dt.timestamp() * 1000000)
            except Exception:
                pass

        if chrome_cookie.get("last_access_utc"):
            try:
                if isinstance(chrome_cookie["last_access_utc"], (int, float)):
                    chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                    access_dt = chrome_epoch + timedelta(
                        microseconds=chrome_cookie["last_access_utc"]
                    )
                    last_access_time = int(access_dt.timestamp() * 1000000)
            except Exception:
                pass

        # Map SameSite values
        samesite_map = {0: 0, 1: 1, 2: 2}  # Chrome: 0=None, 1=Lax, 2=Strict
        samesite = samesite_map.get(chrome_cookie.get("samesite", 0), 0)

        # Handle the host (Firefox needs a leading dot for domain cookies)
        host = chrome_cookie.get("host_key", "")
        if host and not host.startswith(".") and "." in host:
            # Add a leading dot for domain cookies that lack one
            if not any(host.startswith(prefix) for prefix in ["http://", "https://", "localhost"]):
                host = "." + host

        return (
            "",  # originAttributes (empty for regular cookies)
            chrome_cookie.get("name", ""),
            chrome_cookie.get("value", ""),
            host,
            chrome_cookie.get("path", "/"),
            expiry,
            last_access_time,
            creation_time,
            1 if chrome_cookie.get("is_secure") else 0,
            1 if chrome_cookie.get("is_httponly") else 0,
            0,  # inBrowserElement
            samesite,
            samesite,  # rawSameSite
            0,  # schemeMap
            0,  # isPartitionedAttributeSet
        )

    def migrate_chrome_profile_to_camoufox(
        self,
        chrome_profile_path: str,
        camoufox_profile: Profile,
        include_cookies: bool = True,
        include_bookmarks: bool = True,
        include_history: bool = False,
    ) -> dict[str, Any]:
        """Full migration of a Chrome profile into Camoufox."""
        results = {
            "success": False,
            "cookies_imported": 0,
            "bookmarks_imported": 0,
            "history_imported": 0,
            "errors": [],
        }

        try:
            chrome_path = Path(chrome_profile_path)
            camoufox_path = Path(camoufox_profile.get_storage_path())

            # Create the Camoufox profile directory if missing
            camoufox_path.mkdir(parents=True, exist_ok=True)

            # Import cookies
            if include_cookies:
                try:
                    cookies_json = self.export_chrome_cookies(chrome_profile_path)
                    if cookies_json and self.import_cookies_to_camoufox(
                        cookies_json, str(camoufox_path)
                    ):
                        results["cookies_imported"] = 1
                        # Remove the temporary file
                        os.unlink(cookies_json)
                except Exception as e:
                    results["errors"].append(f"Cookie import error: {e}")

            # Import bookmarks
            if include_bookmarks:
                try:
                    bookmarks_path = chrome_path / "Bookmarks"
                    if bookmarks_path.exists():
                        # Copy the bookmarks file (Firefox-format conversion can be added later)
                        shutil.copy2(bookmarks_path, camoufox_path / "chrome_bookmarks.json")
                        results["bookmarks_imported"] = 1
                except Exception as e:
                    results["errors"].append(f"Bookmark import error: {e}")

            # Import history (optional)
            if include_history:
                try:
                    history_path = chrome_path / "History"
                    if history_path.exists():
                        # Export history from the Chrome SQLite database to JSON
                        history_json = self._export_chrome_history(str(history_path))
                        if history_json:
                            with open(camoufox_path / "chrome_history.json", "w") as f:
                                json.dump(history_json, f)
                            results["history_imported"] = len(history_json)
                except Exception as e:
                    results["errors"].append(f"History import error: {e}")

            results["success"] = True
            logger.info(f"Profile migration complete: {results}")

        except Exception as e:
            results["errors"].append(f"General migration error: {e}")
            logger.error(f"Failed to migrate profile: {e}")

        return results

    def _export_chrome_history(self, history_db_path: str) -> list[dict] | None:
        """Export history from Chrome."""
        try:
            # Make a temporary copy
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
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
                history.append(
                    {
                        "url": row[0],
                        "title": row[1],
                        "visit_count": row[2],
                        "last_visit_time": row[3],
                    }
                )

            conn.close()
            os.unlink(temp_history_path)

            return history

        except Exception as e:
            logger.error(f"Failed to export history: {e}")
            return None


# Work around a missing pandas import
try:
    import pandas as pd
except ImportError:
    # Provide a stub for Timedelta
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
