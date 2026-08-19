"""Reusable authentication and authorization dependencies.

Routes declare what they need; none of them query the database themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    CompanySuspendedError,
    InactiveUserError,
    InsufficientPermissionError,
    InsufficientRoleError,
    InvalidTokenError,
    UnauthorizedError,
)
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository

# auto_error=False so a missing header raises OUR UnauthorizedError, in the
# project's envelope, rather than Starlette's bare {"detail": "Not authenticated"}.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="Bearer")

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    """Resolve the caller from `Authorization: Bearer <access_token>`.

    Flow: extract -> decode -> validate signature/expiry/type -> load user.

    The user is loaded from the database on every request rather than trusted
    from the token's claims. That costs one indexed primary-key lookup and buys
    immediate effect for deactivation and role changes — with claims-only
    trust, a disabled account keeps working until its access token expires.
    """
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Missing bearer token.")

    payload = decode_access_token(credentials.credentials)

    raw_sub = payload.get("sub")
    try:
        user_id = uuid.UUID(str(raw_sub))
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError("Token subject is not a valid user id.") from exc

    user = await UserRepository(db).find_by_id(user_id)
    if user is None:
        # The token verifies but its subject is gone (deleted account).
        raise InvalidTokenError("User no longer exists.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_active_user(user: CurrentUser) -> User:
    """The dependency ordinary protected routes should use.

    TWO gates, not one. A disabled account is stopped, and so is every account
    in a suspended company — because suspending a company that leaves its
    members able to read their own invoices has not suspended anything.

    This is deliberately here rather than in `CurrentCompany`. Not every
    protected route needs the company object, so a check that lived only there
    would apply to some routes and not others, and which ones would depend on
    whether a handler happened to need a company id.
    """
    if not user.is_active:
        raise InactiveUserError()

    # `company_id` is null only for the platform owner, who belongs to no
    # company and therefore cannot be suspended by one.
    if user.company_id is not None and (
        user.company is None or not user.company.is_active
    ):
        raise CompanySuspendedError()

    return user


CurrentActiveUser = Annotated[User, Depends(get_current_active_user)]


async def get_optional_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User | None:
    """For endpoints that behave differently when signed in but do not require
    it. Never raises."""
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(db, credentials)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Authorization / RBAC
# ---------------------------------------------------------------------------
# Roles are coarse; permissions are what code should check against. Routes
# declare a permission, so re-shuffling which role holds it is a one-line
# change here rather than a hunt through controllers.
ROLE_PERMISSIONS: dict[UserRole, set[str]] = {
    UserRole.MEMBER: {
        "user.read.self",
        "user.update.self",
        # invoice.read is scoped to the caller's OWN uploads; reading anyone
        # else's requires invoice.read.all. Two permissions rather than one
        # because "can read invoices" is genuinely two different capabilities.
        "invoice.read",
        "invoice.create",
    },
    UserRole.MANAGER: {
        "user.read.self",
        "user.update.self",
        "user.read",
        "invoice.read",
        "invoice.read.all",
        "invoice.create",
        "invoice.approve",
    },
    UserRole.ADMIN: {
        "user.read.self",
        "user.update.self",
        "user.read",
        "user.create",
        "user.update",
        "user.delete",
        "invoice.read",
        "invoice.read.all",
        "invoice.create",
        "invoice.approve",
        "invoice.delete",
        "system.admin",
    },
    # The platform owner. Read this grant as a DENY LIST — what is missing is
    # the point, and it is missing deliberately.
    #
    # No `invoice.*`, no `user.read`, no `system.admin`. Somebody who creates
    # companies has no business inside their payables, and "the platform owner
    # can see everything" is how one forgotten filter becomes one company
    # reading another's ledger. They create a company and its first
    # administrator; from there the company runs itself.
    #
    # Two independent things enforce that. This grant, and `company_of()` —
    # which raises for an account with no company, so even a permission granted
    # here by mistake still cannot resolve a company to scope a query to.
    UserRole.SUPER_ADMIN: {
        "user.read.self",
        "user.update.self",
        "platform.admin",
    },
}


def user_permissions(user: User) -> set[str]:
    return ROLE_PERMISSIONS.get(user.role, set())


def require_role(*roles: UserRole) -> Callable[..., Awaitable[User]]:
    """Dependency factory gating on role.

        @router.get("/admin", dependencies=[Depends(require_role(UserRole.ADMIN))])
    """
    allowed = set(roles)

    async def _dependency(user: CurrentActiveUser) -> User:
        if user.role not in allowed:
            raise InsufficientRoleError(
                f"Requires one of: {', '.join(sorted(r.value for r in allowed))}."
            )
        return user

    return _dependency


def require_permission(*permissions: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory gating on permission. Requires ALL listed.

        @router.delete(
            "/users/{id}",
            dependencies=[Depends(require_permission("user.delete"))],
        )
    """
    required = set(permissions)

    async def _dependency(user: CurrentActiveUser) -> User:
        missing = required - user_permissions(user)
        if missing:
            raise InsufficientPermissionError(
                f"Missing permission(s): {', '.join(sorted(missing))}."
            )
        return user

    return _dependency


# ---------------------------------------------------------------------------
# Request metadata
# ---------------------------------------------------------------------------
def get_client_ip(request: Request) -> str | None:
    """Best-effort client IP for session auditing.

    X-Forwarded-For is only trustworthy when a proxy you control sets it — a
    direct client can forge it freely. Recorded here for audit context, never
    used for an access-control decision.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


def get_user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:512] if ua else None
