"""Bounded, versioned saved-view API input."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from camoufox_pm.core.saved_filters import SavedFilter, ViewConfig

ViewName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SavedFilterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: ViewName
    config: ViewConfig = Field(default_factory=lambda: ViewConfig.model_validate({}))


class SavedFilterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_version: int = Field(ge=0)
    name: ViewName | None = None
    config: ViewConfig | None = None


class SavedFilterList(BaseModel):
    filters: list[SavedFilter]
