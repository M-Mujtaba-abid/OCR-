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

    async def create(self, *, name: str, slug: str) -> Company:
        """Insert and flush — but do NOT commit.

        The caller commits, because creating a company and creating its first
        administrator is one unit of work: a company nobody can sign into is
        not a company, and half of it landing is worse than neither.
        """
        company = Company(name=name.strip(), slug=slug)
        self.db.add(company)
        await self.db.flush()
        await self.db.refresh(company)
        return company

    async def list_all(self) -> list[Company]:
        """Every company, newest first. For the platform console only."""
        stmt = select(Company).order_by(Company.created_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def taken_slugs(self) -> set[str]:
        """Every slug in use, for deriving one that is not.

        The unique index is what actually guarantees it; this only avoids
        offering a name whose slug would fail on insert.
        """
        return set((await self.db.execute(select(Company.slug))).scalars().all())

    async def set_active(self, company: Company, *, is_active: bool) -> Company:
        """Suspend or restore. Does NOT commit."""
        company.is_active = is_active
        await self.db.flush()
        await self.db.refresh(company)
        return company

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
