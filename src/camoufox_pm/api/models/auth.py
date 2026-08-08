"""Auth API models."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials for ``POST /auth/login``."""

    username: str = Field(..., min_length=1, description="Account name")
    password: str = Field(..., min_length=1, description="Account password")


class AuthSessionResponse(BaseModel):
    """The caller's authentication state; also the login response."""

    user_auth_enabled: bool = Field(
        ..., description="Whether any user account exists, i.e. whether login is required"
    )
    authenticated: bool = Field(..., description="Whether this request carries a valid session")
    username: str | None = Field(None, description="The logged-in user, when authenticated")
