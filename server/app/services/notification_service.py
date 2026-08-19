"""Notification business rules.

Never commits. Every method here runs inside the caller's transaction, so a
notification about an invoice cannot survive a rollback of the invoice itself —
"you have a new invoice" pointing at a row that does not exist is worse than no
notification at all.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.lib.logging import get_logger
from app.models.notification import Notification, NotificationType
from app.models.user import UserRole
from app.repositories.notification_repository import NotificationRepository
from app.repositories.user_repository import UserRepository

logger = get_logger(__name__)


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.notifications = NotificationRepository(db)
        self.users = UserRepository(db)

    async def notify_admins(
        self,
        *,
        company_id: uuid.UUID,
        type: NotificationType,
        title: str,
        message: str | None = None,
        match_history_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
    ) -> int:
        """Notify every active admin OF THIS COMPANY.

        Returns how many rows were written.

        Managers are deliberately excluded: they approve matches, they do not
        run the intake queue. Widening this is a one-line change if that turns
        out to be wrong.

        The company is not optional and not inferred. Notification titles carry
        file names and vendor names, so an unscoped fan-out does not merely
        annoy the wrong people — it tells one business what another is buying.
        """
        admin_ids = await self.users.list_ids_by_role(
            UserRole.ADMIN, company_id=company_id
        )
        if not admin_ids:
            # Not an error — a company whose admin has been deactivated, or one
            # created moments ago. Logged because it means uploads are piling
            # up with nobody watching.
            logger.warning(
                "No active admin in company %s to notify for %s",
                company_id,
                type.value,
            )
            return 0

        return await self.notifications.create_many(
            company_id=company_id,
            user_ids=admin_ids,
            type=type,
            title=title,
            message=message,
            match_history_id=match_history_id,
            batch_id=batch_id,
        )

    async def notify_user(
        self,
        *,
        company_id: uuid.UUID,
        user_id: uuid.UUID,
        type: NotificationType,
        title: str,
        message: str | None = None,
        match_history_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
    ) -> Notification:
        return await self.notifications.create(
            company_id=company_id,
            user_id=user_id,
            type=type,
            title=title,
            message=message,
            match_history_id=match_history_id,
            batch_id=batch_id,
        )

    # ------------------------------------------------------------------ reads
    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Notification], int]:
        items = await self.notifications.list_for_user(
            user_id,
            unread_only=unread_only,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await self.notifications.count(user_id, unread_only=unread_only)
        return items, total

    async def unread_count(self, user_id: uuid.UUID) -> int:
        return await self.notifications.count(user_id, unread_only=True)

    # ----------------------------------------------------------------- writes
    async def mark_read(self, *, notification_id: uuid.UUID, user_id: uuid.UUID) -> int:
        marked = await self.notifications.mark_read(notification_id, user_id)
        if marked == 0:
            # Already read is indistinguishable from not-yours here, and that
            # is intentional: telling a caller "that exists but is not yours"
            # confirms the id is real.
            raise NotFoundError("Notification not found.")
        await self.db.commit()
        return marked

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        marked = await self.notifications.mark_all_read(user_id)
        await self.db.commit()
        return marked
