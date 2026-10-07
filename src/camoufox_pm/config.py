"""Application settings loaded from environment variables (prefix ``CPM_``)."""

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from the environment or a ``.env`` file."""

    model_config = SettingsConfigDict(env_prefix="CPM_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    api_key: str | None = None
    db_path: str = "data/profiles.db"
    secret_key: str | None = None
    webui_dir: str | None = None
    # Lifetime of a login session. Sessions are stored server-side, so shortening
    # this takes effect for existing sessions on their next request.
    session_ttl_hours: int = 168
    # Force the Secure flag on the session cookie. The flag is set automatically
    # when the request itself arrived over HTTPS, but behind a TLS-terminating
    # proxy the app sees plain HTTP — set this there.
    secure_cookies: bool = False
    # How long a profile lease survives without a heartbeat, in seconds. The
    # heartbeat renews every 30s while a browser is open, so this is really the
    # window in which an instance that died without releasing anything keeps
    # its profiles locked to the rest of the fleet.
    lease_ttl: int = 120
    backup_interval_hours: int = Field(default=24, ge=1)
    backup_retention: int = Field(default=7, ge=1)

    @field_validator("lease_ttl")
    @classmethod
    def _lease_ttl_outlives_the_heartbeat(cls, value: int) -> int:
        # Two heartbeat intervals. At or below one, a lease expires while the
        # instance holding it is still renewing: the browser keeps running and
        # another instance is free to open the same profile, which is the exact
        # failure the lease exists to prevent. Zero or negative would disable
        # mutual exclusion outright, and silently.
        if value < 60:
            raise ValueError(
                "CPM_LEASE_TTL must be at least 60 seconds (the heartbeat renews every 30s)"
            )
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()


def desktop_data_dir() -> Path:
    """A stable, writable location; never relative to the app bundle or cwd."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Camoufox Profile Manager"
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
            / "Camoufox Profile Manager"
        )
    return (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        / "camoufox-profile-manager"
    )


def bootstrap_desktop() -> None:
    """Configure packaged desktop defaults without changing CLI installations.

    Explicit environment/.env configuration wins. Keep the generated key beside
    the database across upgrades; never generate a replacement for a lost key
    on an existing database.
    """
    from cryptography.fernet import Fernet

    configured = Settings()
    if "db_path" not in configured.model_fields_set:
        os.environ["CPM_DB_PATH"] = str(desktop_data_dir() / "profiles.db")
    get_settings.cache_clear()
    settings = get_settings()
    data_dir = Path(settings.db_path).expanduser().resolve().parent
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not settings.secret_key:
        key_path = data_dir / "secret.key"
        if not key_path.exists() and not Path(settings.db_path).exists():
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
        if key_path.exists():
            key = key_path.read_text().strip()
            Fernet(key.encode())  # Fail clearly instead of starting with a broken key.
            os.environ["CPM_SECRET_KEY"] = key
    get_settings.cache_clear()
