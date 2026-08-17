"""Invoice (match_history) database access. No business logic, no HTTP."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.models.match_history import OPEN_STATUSES, InvoiceStatus, MatchHistory


class MatchHistoryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------ write
    async def create(self, **fields: Any) -> MatchHistory:
        """Insert and flush — but do NOT commit.

        Flushing assigns the id so the caller can reference the row (to attach
        notifications, for instance) while the transaction is still open and
        the whole unit of work can still be rolled back.
        """
        invoice = MatchHistory(**fields)
        self.db.add(invoice)
        await self.db.flush()
        return invoice

    async def update(self, invoice: MatchHistory, **fields: Any) -> MatchHistory:
        for key, value in fields.items():
            if not hasattr(invoice, key):
                raise AttributeError(f"MatchHistory has no field {key!r}")
            setattr(invoice, key, value)
        await self.db.flush()
        return invoice

    # ------------------------------------------------------------------- read
    def _base_query(self) -> Select[tuple[MatchHistory]]:
        # A join, not selectinload. The uploader is a many-to-one, so the join
        # cannot multiply rows — it only carries a few extra columns per row,
        # which is nothing against the ~500 ms a second round trip costs from
        # here. Loading it eagerly is mandatory either way: the relationship is
        # lazy="raise", so serialising `invoice.uploader` without it raises
        # rather than silently emitting an N+1.
        return (
            select(MatchHistory)
            .outerjoin(MatchHistory.uploader)
            .options(contains_eager(MatchHistory.uploader))
        )

    async def find_by_id(
        self, invoice_id: uuid.UUID, *, with_lines: bool = False
    ) -> MatchHistory | None:
        stmt = self._base_query().where(MatchHistory.id == invoice_id)
        if with_lines:
            # Opt-in: list views never touch the lines, and loading them there
            # would be one extra query per page for data nothing renders.
            # `lines` is lazy="raise", so serialising it without this raises
            # rather than quietly emitting an N+1.
            stmt = stmt.options(selectinload(MatchHistory.lines))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------ paged reads
    #
    # One statement per page, not three.
    #
    # A page used to cost a SELECT for the rows, a second for the uploaders
    # (selectinload) and a third for the total. That is defensible when the
    # database is next door; against this one every round trip measured ~500 ms,
    # so three sequential statements were ~2 seconds of a request that did no
    # real work. Collapsed into a join plus a window count: measured 1,984 ms
    # -> 981 ms, and it will still be one round trip when the database moves
    # closer and the numbers shrink.
    #
    # It also fixes a quiet bug: the old admin count ignored `uploaded_by`, so
    # filtering the queue by uploader paginated against everybody's total. The
    # count now comes from the same WHERE clause as the rows, which is a thing
    # that cannot drift.
    def _page_query(self) -> Select[tuple[MatchHistory, int]]:
        return (
            select(MatchHistory, func.count().over().label("total"))
            # contains_eager, not selectinload: the uploader columns ride along
            # on the join, so `lazy="raise"` is satisfied without a second trip.
            .outerjoin(MatchHistory.uploader)
            .options(contains_eager(MatchHistory.uploader))
        )

    async def _page(
        self,
        stmt: Select[tuple[MatchHistory, int]],
        where: list[ColumnElement[bool]],
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[MatchHistory], int]:
        stmt = stmt.where(*where) if where else stmt
        rows = (
            await self.db.execute(
                stmt.order_by(MatchHistory.created_at.desc()).limit(limit).offset(offset)
            )
        ).unique().all()

        if rows:
            return [row[0] for row in rows], int(rows[0].total)

        # A window count comes back with the rows, so an empty page carries no
        # total. Off the end of the list — page 3 after a filter narrows the
        # results — that would report zero and the pager would claim there is
        # nothing here at all. Only then is the extra round trip spent.
        if offset == 0:
            return [], 0
        total = await self.db.scalar(
            select(func.count()).select_from(MatchHistory).where(*where)
        )
        return [], int(total or 0)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
        status: InvoiceStatus | None = None,
    ) -> tuple[list[MatchHistory], int]:
        where: list[ColumnElement[bool]] = [MatchHistory.uploaded_by == user_id]
        if status is not None:
            where.append(MatchHistory.status == status)
        return await self._page(self._page_query(), where, limit=limit, offset=offset)

    async def list_all(
        self,
        *,
        tenant_id: str = "default",
        limit: int = 20,
        offset: int = 0,
        status: InvoiceStatus | None = None,
        open_only: bool = False,
        uploaded_by: uuid.UUID | None = None,
    ) -> tuple[list[MatchHistory], int]:
        where: list[ColumnElement[bool]] = [MatchHistory.tenant_id == tenant_id]
        if status is not None:
            where.append(MatchHistory.status == status)
        elif open_only:
            where.append(MatchHistory.status.in_(OPEN_STATUSES))
        if uploaded_by is not None:
            where.append(MatchHistory.uploaded_by == uploaded_by)
        return await self._page(self._page_query(), where, limit=limit, offset=offset)

    async def count(
        self,
        *,
        tenant_id: str | None = None,
        user_id: uuid.UUID | None = None,
        status: InvoiceStatus | None = None,
        open_only: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(MatchHistory)
        if tenant_id is not None:
            stmt = stmt.where(MatchHistory.tenant_id == tenant_id)
        if user_id is not None:
            stmt = stmt.where(MatchHistory.uploaded_by == user_id)
        if status is not None:
            stmt = stmt.where(MatchHistory.status == status)
        elif open_only:
            stmt = stmt.where(MatchHistory.status.in_(OPEN_STATUSES))
        return int((await self.db.execute(stmt)).scalar_one())

    async def count_by_status(
        self, *, tenant_id: str = "default", user_id: uuid.UUID | None = None
    ) -> dict[InvoiceStatus, int]:
        """One GROUP BY instead of thirteen COUNTs.

        Absent statuses are simply missing from the result — the service
        zero-fills, because the repository reports what the table contains.
        """
        stmt = (
            select(MatchHistory.status, func.count())
            .where(MatchHistory.tenant_id == tenant_id)
            .group_by(MatchHistory.status)
        )
        if user_id is not None:
            stmt = stmt.where(MatchHistory.uploaded_by == user_id)
        return {status: int(n) for status, n in (await self.db.execute(stmt)).all()}
