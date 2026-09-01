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
    # PostgreSQL connection URL. Unset (the default) keeps the SQLite file above;
    # set, every instance pointed at the same database shares profiles — which
    # is what the row-level leases (CPM_LEASE_TTL) exist to make safe.
    db_url: str | None = None
    # Where profile browser data lives. Only derived from db_path's directory
    # when this is not set explicitly: with Postgres there is no db_path, and
    # deriving from it would put profile storage at "/".
    data_dir: str | None = None
    # How long a lease lasts without a heartbeat. The heartbeat renews every 30s,
    # so this is the time a crashed instance's leases take to free themselves.
    # Floored well above that interval: a TTL at or below it expires leases under
    # live browsers, and 0 or negative turns mutual exclusion off silently.
    lease_ttl: int = 120
    secret_key: str | None = None
    webui_dir: str | None = None
    # Lifetime of a login session. Sessions are stored server-side, so shortening
    # this takes effect for existing sessions on their next request.
    session_ttl_hours: int = 168
    # Force the Secure flag on the session cookie. The flag is set automatically
    # when the request itself arrived over HTTPS, but behind a TLS-terminating
    # proxy the app sees plain HTTP — set this there.
    secure_cookies: bool = False

    @field_validator("lease_ttl")
    @classmethod
    def _lease_ttl_outlives_the_heartbeat(cls, value: int) -> int:
        # 60s is two heartbeat intervals: anything at or below one interval
        # expires a lease the owning instance is still renewing, so a browser
        # keeps running while another machine is free to take its profile.
        if value < 60:
            raise ValueError(
                "CPM_LEASE_TTL must be at least 60 seconds (heartbeat renews every 30s)"
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
