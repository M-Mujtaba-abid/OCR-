"""Notification routes — HTTP surface only.

No permission gate here on purpose: notifications are addressed to a specific
user, and every query is scoped by `current_user.id` inside the repository.
There is no "read anyone's notifications" capability to grant, so a permission
check would only be decoration.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.notification_controller import NotificationController
from app.db.session import get_db
from app.dependencies.auth import CurrentActiveUser
from app.lib.responses import ApiErrorResponse, ApiResponse, PaginatedData
from app.schemas.notification import MarkedRead, NotificationRead, UnreadCount
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_notification_controller(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationController:
    return NotificationController(NotificationService(db))


Controller = Annotated[NotificationController, Depends(get_notification_controller)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
}


@router.get(
    "",
    response_model=ApiResponse[PaginatedData[NotificationRead]],
    summary="Your notifications",
    responses=ERROR_RESPONSES,
)
async def list_notifications(
    controller: Controller,
    user: CurrentActiveUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    unread_only: Annotated[bool, Query()] = False,
) -> ApiResponse[PaginatedData[NotificationRead]]:
    return await controller.list(
        user=user, page=page, page_size=page_size, unread_only=unread_only
    )


@router.get(
    "/unread",
    response_model=ApiResponse[UnreadCount],
    summary="Unread notification count",
    responses=ERROR_RESPONSES,
)
async def unread_count(
    controller: Controller, user: CurrentActiveUser
) -> ApiResponse[UnreadCount]:
    # Before /{notification_id} would be, if that route existed — kept adjacent
    # to the collection route for the same reason.
    return await controller.unread_count(user=user)


@router.patch(
    "/read-all",
    response_model=ApiResponse[MarkedRead],
    summary="Mark every notification read",
    responses=ERROR_RESPONSES,
)
async def mark_all_read(
    controller: Controller, user: CurrentActiveUser
) -> ApiResponse[MarkedRead]:
    # Declared before /{notification_id}/read so "read-all" is never parsed as
    # a UUID path parameter.
    return await controller.mark_all_read(user=user)


@router.patch(
    "/{notification_id}/read",
    response_model=ApiResponse[MarkedRead],
    summary="Mark one notification read",
    responses=ERROR_RESPONSES,
)
async def mark_read(
    notification_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    user: CurrentActiveUser,
) -> ApiResponse[MarkedRead]:
    return await controller.mark_read(user=user, notification_id=notification_id)
