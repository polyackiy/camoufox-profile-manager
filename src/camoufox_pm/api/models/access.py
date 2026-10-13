"""Deliberate account roles and explicit export grants."""

from typing import Literal

from pydantic import BaseModel, Field

from camoufox_pm.core.auth import MIN_PASSWORD_LENGTH

Role = Literal["administrator", "operator", "viewer"]


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[\w.@+-]+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)
    role: Role


class UpdateUserRequest(BaseModel):
    role: Role | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=1024)


class GrantRequest(BaseModel):
    can_export: bool = False
