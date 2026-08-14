"""Invoice (match_history) database access. No business logic, no HTTP."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
        # selectinload, not joinedload: the uploader is a many-to-one, and a
        # LEFT JOIN would duplicate the (wide) invoice row for the ORM to
        # de-duplicate. A second small query is cheaper here.
        #
        # It is also mandatory rather than optional — the relationship is
        # lazy="raise", so serialising `invoice.uploader` without this would
        # raise instead of silently emitting N queries.
        return select(MatchHistory).options(selectinload(MatchHistory.uploader))

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

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
        status: InvoiceStatus | None = None,
    ) -> list[MatchHistory]:
        stmt = self._base_query().where(MatchHistory.uploaded_by == user_id)
        if status is not None:
            stmt = stmt.where(MatchHistory.status == status)
        stmt = stmt.order_by(MatchHistory.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_all(
        self,
        *,
        tenant_id: str = "default",
        limit: int = 20,
        offset: int = 0,
        status: InvoiceStatus | None = None,
        open_only: bool = False,
        uploaded_by: uuid.UUID | None = None,
    ) -> list[MatchHistory]:
        stmt = self._base_query().where(MatchHistory.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(MatchHistory.status == status)
        elif open_only:
            stmt = stmt.where(MatchHistory.status.in_(OPEN_STATUSES))
        if uploaded_by is not None:
            stmt = stmt.where(MatchHistory.uploaded_by == uploaded_by)
        stmt = stmt.order_by(MatchHistory.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all())

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
