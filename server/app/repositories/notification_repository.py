"""Notification database access. No business logic, no HTTP."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType


class NotificationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, **fields: Any) -> Notification:
        notification = Notification(**fields)
        self.db.add(notification)
        await self.db.flush()
        return notification

    async def create_many(
        self,
        *,
        company_id: uuid.UUID,
        user_ids: list[uuid.UUID],
        type: NotificationType,
        title: str,
        message: str | None = None,
        match_history_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
    ) -> int:
        """Fan one event out to several recipients in a single flush.

        add_all rather than a loop of add+flush: notifying twenty admins should
        be one round trip, not twenty.

        `company_id` has no default. Every other argument here describes the
        event; this one decides who can ever see it, and a default would be a
        guess at that.
        """
        if not user_ids:
            return 0

        rows = [
            Notification(
                company_id=company_id,
                user_id=user_id,
                type=type,
                title=title,
                message=message,
                match_history_id=match_history_id,
                batch_id=batch_id,
            )
            for user_id in user_ids
        ]
        self.db.add_all(rows)
        await self.db.flush()
        return len(rows)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        stmt = (
            stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def count(self, user_id: uuid.UUID, *, unread_only: bool = False) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id)
        )
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        return int((await self.db.execute(stmt)).scalar_one())

    async def mark_read(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> int:
        """Mark one notification read.

        The user_id is part of the WHERE clause, not checked afterwards: that
        makes it structurally impossible to mark somebody else's notification
        read by guessing an id. A 0 rowcount means "not yours or not there",
        and the caller cannot tell those apart — which is correct.
        """
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=dt.datetime.now(dt.UTC))
        )
        return int((await self.db.execute(stmt)).rowcount or 0)

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=dt.datetime.now(dt.UTC))
        )
        return int((await self.db.execute(stmt)).rowcount or 0)
