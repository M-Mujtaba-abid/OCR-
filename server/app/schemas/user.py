"""User request/response schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class UserRead(BaseModel):
    """Safe user representation — the ONLY user shape the API ever returns.

    There is no `password_hash` field here, and that is the point. Because
    routes declare `response_model=ApiResponse[UserRead]`, FastAPI serialises
    through this model and structurally cannot emit the hash even if a service
    hands back a full ORM object. The protection is by construction, not by
    remembering to strip a field.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
        description=(
            "Minimum 8 characters. The maximum is a denial-of-service guard: "
            "Argon2 cost scales with input length, so an unbounded password "
            "field lets one request burn arbitrary CPU."
        ),
    )
    full_name: str | None = Field(default=None, max_length=255)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = None
