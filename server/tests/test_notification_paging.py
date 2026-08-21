"""The paging contract the bell's infinite scroll is built on.

The dropdown loads twelve rows at a time and asks for the next page as the
reader approaches the end. Whether there IS a next page is decided entirely by
`pagination.pages` in the response — the client compares it against
`pagination.page` and stops when they meet.

So these are not tests of a list endpoint in general. They are tests of the three
things that would break infinite scroll specifically: a wrong `pages` count
(scrolls forever, or stops early and hides rows), pages that overlap or skip
(duplicate or missing rows), and a page past the end that errors instead of
coming back empty.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.user import User
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

NOTIFICATIONS = "/api/v1/notifications"
PAGE_SIZE = 12


async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


async def _seed(db: AsyncSession, user: User, count: int) -> list[str]:
    """`count` notifications, newest first, with distinguishable titles.

    `created_at` is stamped explicitly and spaced a minute apart. The endpoint
    orders by it, and rows written in the same transaction would otherwise share
    a timestamp — leaving the order undefined and the paging assertions below
    testing nothing.
    """
    assert user.company_id is not None
    now = dt.datetime.now(dt.UTC)
    rows = [
        Notification(
            company_id=user.company_id,
            user_id=user.id,
            type=NotificationType.INVOICE_UPLOADED,
            title=f"row {index:03d}",
            created_at=now - dt.timedelta(minutes=index),
        )
        for index in range(count)
    ]
    db.add_all(rows)
    await db.commit()
    # Newest first is what the endpoint returns, and index 0 is the newest.
    return [f"row {index:03d}" for index in range(count)]


async def _page(client: AsyncClient, headers: dict[str, str], page: int) -> dict:
    response = await client.get(
        NOTIFICATIONS,
        headers=headers,
        params={"page": page, "page_size": PAGE_SIZE},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


class TestPagingContract:
    async def test_pages_counts_the_partial_last_page(
        self, client: AsyncClient, db: AsyncSession, existing_user: User, password: str
    ) -> None:
        """30 rows at 12 a page is three pages, not two.

        `getNextPageParam` stops the moment `page == pages`, so an undercount
        here does not slow the feed down — it makes the last six rows
        permanently unreachable, silently.
        """
        await _seed(db, existing_user, 30)
        headers = await _token(client, existing_user, password)

        first = await _page(client, headers, 1)
        assert first["pagination"]["total"] >= 30
        expected = -(-first["pagination"]["total"] // PAGE_SIZE)  # ceil
        assert first["pagination"]["pages"] == expected

    async def test_consecutive_pages_do_not_overlap_or_skip(
        self, client: AsyncClient, db: AsyncSession, existing_user: User, password: str
    ) -> None:
        """The flattened feed is pages concatenated, so an overlap is a duplicate
        React key and a gap is a row nobody ever sees."""
        titles = await _seed(db, existing_user, 30)
        headers = await _token(client, existing_user, password)

        first = await _page(client, headers, 1)
        second = await _page(client, headers, 2)

        first_titles = [item["title"] for item in first["items"]]
        second_titles = [item["title"] for item in second["items"]]

        assert len(first_titles) == PAGE_SIZE
        assert not set(first_titles) & set(second_titles)
        # And they are the right rows, in the right order: newest first.
        assert first_titles == titles[:PAGE_SIZE]
        assert second_titles[: len(titles) - PAGE_SIZE][:PAGE_SIZE] == (
            titles[PAGE_SIZE : PAGE_SIZE * 2]
        )

    async def test_a_page_past_the_end_is_empty_not_an_error(
        self, client: AsyncClient, db: AsyncSession, existing_user: User, password: str
    ) -> None:
        """The client can race past the end — a refetch shrinking the feed while
        a scroll is already fetching. That has to be a quiet empty page."""
        await _seed(db, existing_user, 5)
        headers = await _token(client, existing_user, password)

        far = await _page(client, headers, 99)
        assert far["items"] == []
        assert far["pagination"]["page"] == 99

    async def test_an_empty_feed_still_reports_one_page(
        self, client: AsyncClient, admin_user: User, password: str
    ) -> None:
        """`pages` is floored at 1, so a brand-new account does not report zero
        pages — which would read as "page 1 does not exist"."""
        headers = await _token(client, admin_user, password)
        first = await _page(client, headers, 1)
        assert first["pagination"]["pages"] >= 1

    async def test_the_feed_is_scoped_to_the_caller(
        self,
        client: AsyncClient,
        db: AsyncSession,
        existing_user: User,
        admin_user: User,
        password: str,
    ) -> None:
        """Paging deeper must not become a way to walk into somebody else's.

        Worth asserting precisely because the routes carry no permission gate at
        all — every one of them is scoped by `current_user.id` inside the
        repository, and this is the test that says so.
        """
        marker = f"private-{uuid.uuid4().hex[:8]}"
        assert existing_user.company_id is not None
        db.add(
            Notification(
                company_id=existing_user.company_id,
                user_id=existing_user.id,
                type=NotificationType.INVOICE_UPLOADED,
                title=marker,
            )
        )
        await db.commit()

        others = await _token(client, admin_user, password)
        for page in (1, 2, 3):
            body = await _page(client, others, page)
            assert marker not in {item["title"] for item in body["items"]}
