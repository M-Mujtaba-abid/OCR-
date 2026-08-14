"""Notification controller: HTTP in, HTTP out."""

from __future__ import annotations

import math
import uuid

from app.lib.responses import ApiResponse, PaginatedData, PaginationMeta
from app.models.user import User
from app.schemas.notification import MarkedRead, NotificationRead, UnreadCount
from app.services.notification_service import NotificationService


class NotificationController:
    def __init__(self, service: NotificationService) -> None:
        self.service = service

    async def list(
        self, *, user: User, page: int, page_size: int, unread_only: bool
    ) -> ApiResponse[PaginatedData[NotificationRead]]:
        items, total = await self.service.list_for_user(
            user.id, unread_only=unread_only, page=page, page_size=page_size
        )
        return ApiResponse.ok(
            PaginatedData[NotificationRead](
                items=[NotificationRead.model_validate(n) for n in items],
                pagination=PaginationMeta(
                    page=page,
                    page_size=page_size,
                    total=total,
                    pages=max(1, math.ceil(total / page_size)),
                ),
            ),
            message="Notifications retrieved",
        )

    async def unread_count(self, *, user: User) -> ApiResponse[UnreadCount]:
        return ApiResponse.ok(
            UnreadCount(count=await self.service.unread_count(user.id)),
            message="Unread count retrieved",
        )

    async def mark_read(
        self, *, user: User, notification_id: uuid.UUID
    ) -> ApiResponse[MarkedRead]:
        marked = await self.service.mark_read(
            notification_id=notification_id, user_id=user.id
        )
        return ApiResponse.ok(MarkedRead(marked=marked), message="Marked as read")

    async def mark_all_read(self, *, user: User) -> ApiResponse[MarkedRead]:
        marked = await self.service.mark_all_read(user.id)
        return ApiResponse.ok(
            MarkedRead(marked=marked), message=f"{marked} marked as read"
        )
