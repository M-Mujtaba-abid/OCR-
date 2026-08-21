"""Notifications are the only table nothing ever deleted from.

Everything else here either stays because it is an accounting record — invoices,
approval requests, decisions — or is bounded by something. Notifications were
neither: one row per recipient per event, forever, and every unread count walked
past all of them.

The rule under test is deliberately half about age and half about read-state.
Deleting something somebody has already seen loses nothing, because what
happened is recorded on the invoice and the approval request; the notification
was the nudge that pointed at them. Deleting something nobody opened is the one
version a person cannot recover from, so it never happens by age.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.company import Company
from app.models.notification import Notification, NotificationType
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.services.notification_service import NotificationService

pytestmark = pytest.mark.asyncio

CLEANUP = "/internal/cron/cleanup"
RETENTION_DAYS = 90


async def _notification(
    db: AsyncSession,
    user: User,
    *,
    age_days: float,
    is_read: bool,
    title: str = "test",
) -> Notification:
    """One notification of a given age and read-state.

    `created_at` is passed rather than left to the server default, because the
    whole rule is about age and there is no other way to have a row that is
    ninety days old inside a test that runs in a second.
    """
    assert user.company_id is not None
    row = Notification(
        company_id=user.company_id,
        user_id=user.id,
        type=NotificationType.INVOICE_UPLOADED,
        title=title,
        is_read=is_read,
        read_at=dt.datetime.now(dt.UTC) if is_read else None,
        created_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=age_days),
    )
    db.add(row)
    await db.commit()
    return row


async def _exists(db: AsyncSession, notification_id: uuid.UUID) -> bool:
    found = (
        await db.execute(
            select(Notification).where(Notification.id == notification_id)
        )
    ).scalar_one_or_none()
    return found is not None


class TestRetentionRule:
    async def test_a_read_notification_past_the_window_is_deleted(
        self, db: AsyncSession, existing_user: User
    ) -> None:
        old = await _notification(
            db, existing_user, age_days=RETENTION_DAYS + 5, is_read=True
        )
        await NotificationService(db).purge_read(older_than_days=RETENTION_DAYS)
        assert not await _exists(db, old.id)

    async def test_an_unread_notification_of_the_same_age_survives(
        self, db: AsyncSession, existing_user: User
    ) -> None:
        """The asymmetry is the whole point. Age alone is not enough — nobody
        should lose something they never saw because it got old."""
        never_opened = await _notification(
            db, existing_user, age_days=RETENTION_DAYS + 5, is_read=False
        )
        await NotificationService(db).purge_read(older_than_days=RETENTION_DAYS)
        assert await _exists(db, never_opened.id)

    async def test_a_read_notification_inside_the_window_survives(
        self, db: AsyncSession, existing_user: User
    ) -> None:
        recent = await _notification(
            db, existing_user, age_days=RETENTION_DAYS - 5, is_read=True
        )
        await NotificationService(db).purge_read(older_than_days=RETENTION_DAYS)
        assert await _exists(db, recent.id)

    async def test_the_boundary_is_older_than_not_at_least(
        self, db: AsyncSession, existing_user: User
    ) -> None:
        """Exactly at the cutoff is inside the window. Stated because a `<` and a
        `<=` are one keystroke apart and the difference is invisible until a
        test names it."""
        just_inside = await _notification(
            db, existing_user, age_days=RETENTION_DAYS - 0.01, is_read=True
        )
        just_outside = await _notification(
            db, existing_user, age_days=RETENTION_DAYS + 0.01, is_read=True
        )
        await NotificationService(db).purge_read(older_than_days=RETENTION_DAYS)
        assert await _exists(db, just_inside.id)
        assert not await _exists(db, just_outside.id)

    async def test_it_reports_how_many_it_removed(
        self, db: AsyncSession, existing_user: User
    ) -> None:
        for index in range(3):
            await _notification(
                db,
                existing_user,
                age_days=RETENTION_DAYS + 1,
                is_read=True,
                title=f"old {index}",
            )
        await _notification(
            db, existing_user, age_days=1, is_read=True, title="recent"
        )

        removed = await NotificationService(db).purge_read(
            older_than_days=RETENTION_DAYS
        )
        # At least three: the database is shared with whatever other tests left
        # behind, and this asserts the count is real rather than exact.
        assert removed >= 3


class TestCrossCompany:
    async def test_it_deletes_across_companies_and_spares_recent_rows(
        self, db: AsyncSession, existing_user: User, password: str
    ) -> None:
        """Deliberately global — the scheduler has no company to scope to.

        Safe for a stronger reason than the invoice sweep's: the sweep reads
        nothing company-SPECIFIC, this reads nothing at all. Age and read-state
        are the entire predicate, and a statement that returns no rows cannot
        expose one company's data to another.

        So both halves are asserted: an old read row in the RIVAL company is
        removed too, and a recent row in each company survives.
        """
        rival = Company(name="Rivals", slug=f"rival-{uuid.uuid4().hex[:8]}")
        db.add(rival)
        await db.flush()
        outsider = await UserRepository(db).create(
            company_id=rival.id,
            email=f"rival-{uuid.uuid4().hex[:12]}@example.com",
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
        )

        rival_old = await _notification(
            db, outsider, age_days=RETENTION_DAYS + 5, is_read=True
        )
        rival_new = await _notification(db, outsider, age_days=1, is_read=True)
        ours_old = await _notification(
            db, existing_user, age_days=RETENTION_DAYS + 5, is_read=True
        )
        ours_new = await _notification(db, existing_user, age_days=1, is_read=True)

        await NotificationService(db).purge_read(older_than_days=RETENTION_DAYS)

        assert not await _exists(db, rival_old.id)
        assert not await _exists(db, ours_old.id)
        assert await _exists(db, rival_new.id)
        assert await _exists(db, ours_new.id)


class TestEndpoint:
    async def test_without_the_secret_it_refuses(self, client: AsyncClient) -> None:
        """Same guard as the sweep, and it matters as much: an unauthenticated
        endpoint that deletes rows is worse than one that merely re-queues."""
        response = await client.get(CLEANUP)
        assert response.status_code == 401, response.text

    async def test_a_wrong_secret_refuses(self, client: AsyncClient) -> None:
        response = await client.get(
            CLEANUP, headers={"Authorization": "Bearer not-the-secret"}
        )
        assert response.status_code == 401, response.text

    async def test_it_is_not_in_the_public_schema(self, client: AsyncClient) -> None:
        """Platform plumbing, not part of the product's API — the same reason
        the sweep sets include_in_schema=False."""
        spec = (await client.get("/openapi.json")).json()
        assert CLEANUP not in spec["paths"]
