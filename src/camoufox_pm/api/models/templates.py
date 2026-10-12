"""Template requests deliberately have no proxy, fingerprint or browser-data fields."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from camoufox_pm.core.models import BrowserSettings, ProfileTags, normalize_tags


class TemplateSettings(BrowserSettings):
    model_config = ConfigDict(extra="forbid")


class TemplateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    browser_settings: TemplateSettings = Field(default_factory=TemplateSettings)
    notes: str | None = Field(None, max_length=1000)
    tags: ProfileTags = Field(default_factory=list, max_length=20)
    extension_ids: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Template name cannot be empty")
        return value.strip()

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value):
        return normalize_tags(value)


class TemplateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_version: int = Field(ge=0)
    name: str | None = Field(None, min_length=1, max_length=255)
    browser_settings: TemplateSettings | None = None
    notes: str | None = Field(None, max_length=1000)
    tags: ProfileTags | None = Field(None, max_length=20)
    extension_ids: list[str] | None = Field(None, max_length=16)

    @model_validator(mode="after")
    def required_fields(self):
        for field in ("name", "browser_settings", "tags", "extension_ids"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("Only template notes may be cleared with null")
        if self.name is not None:
            self.name = TemplateCreateRequest.nonempty_name(self.name)
        if self.tags is not None:
            self.tags = TemplateCreateRequest.valid_tags(self.tags)
        return self


class TemplateFromProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=255)
    profile_row_version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        return TemplateCreateRequest.nonempty_name(value)


class TemplateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=255)
    group: str | None = Field(None, max_length=255)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        return TemplateCreateRequest.nonempty_name(value)
