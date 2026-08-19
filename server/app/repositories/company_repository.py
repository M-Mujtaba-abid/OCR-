"""Company (tenant) database access. No business logic, no HTTP."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company


class CompanyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def find_by_id(self, company_id: uuid.UUID) -> Company | None:
        return await self.db.get(Company, company_id)

    async def find_by_slug(self, slug: str) -> Company | None:
        stmt = select(Company).where(Company.slug == slug.strip().lower())
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def sole_active(self) -> Company | None:
        """The only active company, or None when there is not exactly one.

        None for zero and None for many, deliberately — the caller's question
        is "is there an unambiguous company here", and both answers to that are
        no. Reading two rows rather than counting is enough to tell them apart
        and costs one row.
        """
        stmt = select(Company).where(Company.is_active.is_(True)).limit(2)
        companies = (await self.db.execute(stmt)).scalars().all()
        return companies[0] if len(companies) == 1 else None
