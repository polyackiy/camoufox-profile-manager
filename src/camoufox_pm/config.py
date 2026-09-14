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
    # How long a profile lease survives without a heartbeat, in seconds. The
    # heartbeat renews every 30s while a browser is open, so this is really the
    # window in which an instance that died without releasing anything keeps
    # its profiles locked to the rest of the fleet.
    lease_ttl: int = 120

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
