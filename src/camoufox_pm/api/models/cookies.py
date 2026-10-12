"""Bounded cookie transfer requests; cookie values are never API metadata."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CookiePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["json", "netscape"]
    text: str = Field(max_length=2 * 1024 * 1024, repr=False, json_schema_extra={"writeOnly": True})


class CookieImportRequest(CookiePreviewRequest):
    mode: Literal["merge", "replace"] = "merge"
    row_version: int = Field(ge=0)
    confirm_replace: bool = False


class CookieSkip(BaseModel):
    row: int
    reason: str


class CookiePreviewData(BaseModel):
    total: int
    importable: int
    session_count: int
    skipped: list[CookieSkip]


class CookieImportData(BaseModel):
    imported: int
    session_count: int
    skipped: list[CookieSkip]
    row_version: int
    backup_id: str | None
