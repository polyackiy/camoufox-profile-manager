"""Application settings loaded from environment variables (prefix ``CPM_``)."""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
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
