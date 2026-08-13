"""The project's standard response envelope.

The Python equivalent of Express's `ApiResponse` — but built as a Pydantic
generic rather than a helper that hand-rolls a dict.

Why that matters: FastAPI generates OpenAPI from `response_model`. A middleware
that wrapped every response body would produce documentation that lied about
the payload shape, and would also corrupt streaming and file responses. Making
the envelope a real model instead means `response_model=ApiResponse[UserRead]`
documents itself correctly and stays type-checked end to end.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Success envelope.

        {"success": true, "message": "Login successful", "data": {...}}
    """

    success: bool = True
    message: str = "OK"
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = "OK") -> "ApiResponse[T]":
        return cls(success=True, message=message, data=data)


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable code for the client.")
    details: object | None = Field(
        default=None,
        description="Field-level validation errors, or null. Never a stack trace.",
    )


class ApiErrorResponse(BaseModel):
    """Error envelope.

        {"success": false, "message": "User not found",
         "error": {"code": "USER_NOT_FOUND"}}

    Declared as a model so it can be attached to route `responses={...}` and
    show up correctly in the OpenAPI schema.
    """

    success: bool = False
    message: str
    error: ErrorDetail
    request_id: str | None = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class PaginatedData(BaseModel, Generic[T]):
    """Body for list endpoints: `ApiResponse[PaginatedData[UserRead]]`."""

    items: list[T]
    pagination: PaginationMeta
