"""User administration routes — HTTP surface only.

Every route here is gated by a PERMISSION, never by a role name. That keeps the
question "who may list users?" answerable in one place (ROLE_PERMISSIONS in
app/dependencies/auth.py) instead of being spread across route decorators, and
means granting managers a new capability is a one-line change there.

`user.read`   -> manager, admin
`user.update` -> admin only
`user.create` -> admin only

Every route is also scoped to the caller's own company. The permission decides
whether they may manage users at all; the company decides which ones — and that
second half comes from the authenticated caller, never from the request.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.user_controller import UserController
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.lib.responses import ApiErrorResponse, ApiResponse, PaginatedData
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserRead, UserRoleUpdate, UserStats
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_controller(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserController:
    return UserController(UserService(db))


Controller = Annotated[UserController, Depends(get_user_controller)]

# The dependency returns the User, so the handler receives the authenticated
# caller AND the permission is enforced — one dependency, not two.
CanReadUsers = Annotated[User, Depends(require_permission("user.read"))]
CanUpdateUsers = Annotated[User, Depends(require_permission("user.update"))]
# `user.create` was granted to admins from the start and had nothing behind it
# until public sign-up was removed. This is what it was for.
CanCreateUsers = Annotated[User, Depends(require_permission("user.create"))]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
}


@router.get(
    "",
    response_model=ApiResponse[PaginatedData[UserRead]],
    summary="List users (requires user.read)",
    responses=ERROR_RESPONSES,
)
async def list_users(
    controller: Controller,
    actor: CanReadUsers,
    page: Annotated[int, Query(ge=1)] = 1,
    # Capped so a client cannot ask for the entire table in one request.
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    role: Annotated[UserRole | None, Query()] = None,
) -> ApiResponse[PaginatedData[UserRead]]:
    # The actor is passed, not merely required: every read below is scoped to
    # the company they belong to. There is no query parameter for company, and
    # there is no call that means "everybody's".
    return await controller.list_users(
        actor=actor, page=page, page_size=page_size, role=role
    )


@router.post(
    "",
    response_model=ApiResponse[UserRead],
    status_code=status.HTTP_201_CREATED,
    summary="Add a user to your company (requires user.create)",
    responses=ERROR_RESPONSES,
)
async def create_user(
    payload: UserCreate,
    controller: Controller,
    actor: CanCreateUsers,
) -> ApiResponse[UserRead]:
    """The only way an account is created.

    Public sign-up was removed when the system became multi-company: a form
    filled in by a stranger cannot say which business they work for, and
    guessing puts an uninvited account inside somebody's payables.
    """
    return await controller.create_user(actor=actor, payload=payload)


@router.get(
    "/stats",
    response_model=ApiResponse[UserStats],
    summary="Aggregate user counts (requires user.read)",
    responses=ERROR_RESPONSES,
)
async def user_stats(
    controller: Controller, actor: CanReadUsers
) -> ApiResponse[UserStats]:
    # Declared BEFORE /{user_id} on purpose: FastAPI matches in declaration
    # order, and the reverse order would make "stats" parse as a user id and
    # return a 422 instead of this route.
    return await controller.get_stats(actor=actor)


@router.get(
    "/{user_id}",
    response_model=ApiResponse[UserRead],
    summary="Read one user (requires user.read)",
    responses=ERROR_RESPONSES,
)
async def get_user(
    user_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    actor: CanReadUsers,
) -> ApiResponse[UserRead]:
    # A user in another company answers 404, not 403 — see `UserService.get_user`.
    return await controller.get_user(actor=actor, user_id=user_id)


@router.patch(
    "/{user_id}/role",
    response_model=ApiResponse[UserRead],
    summary="Change a user's role (requires user.update)",
    responses=ERROR_RESPONSES,
)
async def change_role(
    user_id: Annotated[uuid.UUID, Path()],
    payload: UserRoleUpdate,
    controller: Controller,
    actor: CanUpdateUsers,
) -> ApiResponse[UserRead]:
    return await controller.change_role(actor=actor, user_id=user_id, payload=payload)


@router.patch(
    "/{user_id}/activate",
    response_model=ApiResponse[UserRead],
    summary="Enable an account (requires user.update)",
    responses=ERROR_RESPONSES,
)
async def activate_user(
    user_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    actor: CanUpdateUsers,
) -> ApiResponse[UserRead]:
    return await controller.set_active(actor=actor, user_id=user_id, is_active=True)


@router.patch(
    "/{user_id}/deactivate",
    response_model=ApiResponse[UserRead],
    summary="Disable an account (requires user.update)",
    responses=ERROR_RESPONSES,
)
async def deactivate_user(
    user_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    actor: CanUpdateUsers,
) -> ApiResponse[UserRead]:
    return await controller.set_active(actor=actor, user_id=user_id, is_active=False)
