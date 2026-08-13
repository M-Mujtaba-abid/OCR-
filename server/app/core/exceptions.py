"""Application exception hierarchy.

The Python equivalent of Express's `ApiError`. Every exception carries three
things: the HTTP status, a stable machine-readable `code` the frontend can
branch on, and a human-readable message safe to show a user.

Idiomatic difference from Express worth noting: there is no `asyncHandler`
wrapper here and none is needed. Express requires it because a rejected promise
in an async handler is not routed to the error middleware automatically.
Starlette awaits every endpoint inside its own try/except, so an exception
raised anywhere in a request — including deep inside a repository — propagates
to the handlers registered in `main.py` on its own. Raising is the mechanism.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base for every expected failure.

    Raise these freely from any layer. `main.py` translates them into the
    project's standard error envelope.
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.details = details
        self.headers = headers
        super().__init__(self.message)


# --------------------------------------------------------------------------
# Generic
# --------------------------------------------------------------------------
class BadRequestError(AppError):
    status_code, code, message = 400, "BAD_REQUEST", "Invalid request."


class ValidationError(AppError):
    status_code, code, message = 422, "VALIDATION_ERROR", "Validation failed."


class NotFoundError(AppError):
    status_code, code, message = 404, "NOT_FOUND", "Resource not found."


class ConflictError(AppError):
    status_code, code, message = 409, "CONFLICT", "Resource conflict."


# --------------------------------------------------------------------------
# Authentication (401) — "we do not know who you are"
# --------------------------------------------------------------------------
class UnauthorizedError(AppError):
    status_code, code, message = 401, "UNAUTHORIZED", "Authentication required."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        # RFC 6750: a 401 on a Bearer-protected resource must carry this header.
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class InvalidCredentialsError(UnauthorizedError):
    code = "INVALID_CREDENTIALS"
    message = "Invalid email or password."


class InvalidTokenError(UnauthorizedError):
    code = "INVALID_TOKEN"
    message = "Invalid or malformed token."


class TokenExpiredError(UnauthorizedError):
    code = "TOKEN_EXPIRED"
    message = "Token has expired."


class InvalidRefreshTokenError(UnauthorizedError):
    code = "INVALID_REFRESH_TOKEN"
    message = "Refresh token is missing, invalid, or expired."


class RefreshTokenReusedError(UnauthorizedError):
    """A refresh token was presented that had already been rotated away.

    Treated as theft: the legitimate client would be holding the newest token,
    so seeing an old one means someone captured it. The service revokes the
    whole session family in response.
    """

    code = "REFRESH_TOKEN_REUSED"
    message = "Refresh token reuse detected. All sessions have been revoked."


class InactiveUserError(UnauthorizedError):
    code = "INACTIVE_USER"
    message = "This account is disabled."


# --------------------------------------------------------------------------
# Authorization (403) — "we know who you are, and you may not"
# --------------------------------------------------------------------------
class ForbiddenError(AppError):
    status_code, code, message = 403, "FORBIDDEN", "You do not have permission."


class InsufficientRoleError(ForbiddenError):
    code = "INSUFFICIENT_ROLE"
    message = "Your role does not permit this action."


class InsufficientPermissionError(ForbiddenError):
    code = "INSUFFICIENT_PERMISSION"
    message = "You lack the required permission."


# --------------------------------------------------------------------------
# Domain-specific
# --------------------------------------------------------------------------
class EmailAlreadyRegisteredError(ConflictError):
    code = "EMAIL_ALREADY_REGISTERED"
    message = "An account with this email already exists."


class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"
    message = "User not found."
