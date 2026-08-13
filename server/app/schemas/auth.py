"""Authentication request/response schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.user import UserRead


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenData(BaseModel):
    """Login / refresh payload.

    Note what is absent: the refresh token. It travels only in an HttpOnly
    cookie set by the controller, so JavaScript can never read it. Returning it
    in the body would defeat that entirely.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")
    expires_at: dt.datetime


class LoginData(TokenData):
    user: UserRead


class SessionRead(BaseModel):
    """A device/session row, for the 'where am I signed in' screen.

    `refresh_token_hash` is deliberately not exposed — it is a credential
    equivalent, not metadata.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: dt.datetime
    expires_at: dt.datetime
    last_used_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class LogoutData(BaseModel):
    revoked_sessions: int
