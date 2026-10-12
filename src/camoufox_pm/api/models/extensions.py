"""Local extension-library and closed-profile assignment requests."""

from pydantic import BaseModel, ConfigDict, Field


class ExtensionAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_version: int = Field(ge=0)
    extension_ids: list[str] = Field(default_factory=list, max_length=16)

    retained_refs: list[str] = Field(default_factory=list, max_length=16)
