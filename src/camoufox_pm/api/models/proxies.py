"""Proxy inventory API; credentials are write-only and never returned."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from camoufox_pm.core.models import ProxyCheckRecord, ProxyType
from camoufox_pm.core.proxy_import import normalize_proxy_server


class ProxyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    type: ProxyType
    server: str = Field(min_length=1, max_length=512)
    username: str | None = Field(None, max_length=255)
    password: str | None = Field(
        None, max_length=4096, repr=False, json_schema_extra={"writeOnly": True}
    )
    country: str | None = Field(None, max_length=64)

    @field_validator("username", "password")
    @classmethod
    def valid_credentials(cls, value: str | None) -> str | None:
        if value and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Credentials cannot contain control characters")
        return value or None

    @field_validator("server")
    @classmethod
    def normalize_server(cls, value: str) -> str:
        return normalize_proxy_server(value)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Proxy name cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def paired_credentials(self):
        if self.password and not self.username:
            raise ValueError("A proxy password requires a username")
        return self


class ProxyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_version: int = Field(ge=0)
    name: str | None = Field(None, min_length=1, max_length=255)
    type: ProxyType | None = None
    server: str | None = Field(None, min_length=1, max_length=512)
    username: str | None = Field(None, max_length=255)
    password: str | None = Field(
        None, max_length=4096, repr=False, json_schema_extra={"writeOnly": True}
    )
    country: str | None = Field(None, max_length=64)

    @field_validator("username", "password")
    @classmethod
    def valid_credentials(cls, value: str | None) -> str | None:
        if value and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Credentials cannot contain control characters")
        return value or None

    @field_validator("server")
    @classmethod
    def normalize_server(cls, value: str | None) -> str | None:
        return normalize_proxy_server(value) if value is not None else None

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Proxy name cannot be empty")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def required_values(self):
        for field in ("name", "type", "server"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("Proxy name, type and server cannot be null")
        return self


class ProxyResponse(BaseModel):
    id: str
    name: str
    type: ProxyType
    server: str
    username: str | None
    country: str | None
    has_password: bool
    row_version: int
    last_check: ProxyCheckRecord | None
    created_at: datetime
    updated_at: datetime


class ProxyListData(BaseModel):
    proxies: list[ProxyResponse]


class ProfileAssignmentTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=255)
    row_version: int = Field(ge=0)


class ProxyAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profiles: list[ProfileAssignmentTarget] = Field(min_length=1, max_length=100)
    proxy_row_version: int = Field(ge=0)

    @field_validator("profiles")
    @classmethod
    def distinct_profiles(cls, value):
        if len({profile.id for profile in value}) != len(value):
            raise ValueError("Select each profile only once")
        return value


class AssignmentSkip(BaseModel):
    id: str
    reason: str


class ProxyAssignData(BaseModel):
    assigned: list[str]
    skipped: list[AssignmentSkip]


class ProxyImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(
        min_length=1, max_length=64000, repr=False, json_schema_extra={"writeOnly": True}
    )


class ImportSkip(BaseModel):
    line: int
    reason: str


class ProxyImportData(BaseModel):
    created: list[ProxyResponse]
    skipped: list[ImportSkip]
