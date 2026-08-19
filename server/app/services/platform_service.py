"""The platform owner's operations: creating and suspending companies.

The ONLY place in this system where a company id comes from somewhere other
than the caller's own session — because the caller belongs to no company, and
creating one is precisely the act of naming a company that does not exist yet.

That makes the permission gate on these routes the whole boundary, so read this
module with that in mind: everything here is reachable only by `platform.admin`,
which only `SUPER_ADMIN` holds, and which is granted alongside nothing else.

Note what is absent. There is no method here that reads an invoice, a bill, a
notification or a vendor. The platform owner sets a company up and hands it
over; from there the company's own administrator runs it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.core.slug import unique_slug
from app.lib.logging import get_logger
from app.models.company import Company, CompanyOdooConfig
from app.models.user import User, UserRole
from app.repositories.company_repository import CompanyRepository
from app.repositories.user_repository import UserRepository
from app.schemas.platform import PlatformCompany, PlatformStats

logger = get_logger(__name__)


class PlatformService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.companies = CompanyRepository(db)
        self.users = UserRepository(db)

    # ------------------------------------------------------------------ read
    async def list_companies(self) -> list[PlatformCompany]:
        """Every company, with the counts the console shows.

        The counts are three grouped queries over the whole table rather than
        per company, so the page costs the same at two companies as at fifty.
        """
        companies = await self.companies.list_all()
        if not companies:
            return []

        totals = await self._user_counts()
        configured = await self._companies_with_odoo()

        return [
            PlatformCompany(
                id=company.id,
                name=company.name,
                slug=company.slug,
                is_active=company.is_active,
                created_at=company.created_at,
                user_count=totals.get(company.id, (0, 0, 0))[0],
                active_user_count=totals.get(company.id, (0, 0, 0))[1],
                admin_count=totals.get(company.id, (0, 0, 0))[2],
                odoo_configured=company.id in configured,
            )
            for company in companies
        ]

    async def _user_counts(self) -> dict[uuid.UUID, tuple[int, int, int]]:
        """(total, active, admins) per company, in one statement."""
        stmt = (
            select(
                User.company_id,
                func.count(),
                func.count().filter(User.is_active.is_(True)),
                func.count().filter(User.role == UserRole.ADMIN),
            )
            .where(User.company_id.is_not(None))
            .group_by(User.company_id)
        )
        rows = (await self.db.execute(stmt)).all()
        return {row[0]: (int(row[1]), int(row[2]), int(row[3])) for row in rows}

    async def _companies_with_odoo(self) -> set[uuid.UUID]:
        """Which companies have their own Odoo. Ids only — never credentials."""
        stmt = select(CompanyOdooConfig.company_id)
        return set((await self.db.execute(stmt)).scalars().all())

    async def stats(self) -> PlatformStats:
        companies = await self.companies.list_all()
        total_users = await self.db.scalar(select(func.count()).select_from(User))
        return PlatformStats(
            companies=len(companies),
            active_companies=sum(1 for c in companies if c.is_active),
            users=int(total_users or 0),
        )

    async def get_company(self, company_id: uuid.UUID) -> Company:
        company = await self.companies.find_by_id(company_id)
        if company is None:
            raise NotFoundError("Company not found.", code="COMPANY_NOT_FOUND")
        return company

    # ----------------------------------------------------------------- write
    async def create_company(
        self,
        *,
        name: str,
        admin_email: str,
        admin_password: str,
        admin_full_name: str | None,
    ) -> tuple[Company, User]:
        """A new company and the administrator who will run it, atomically.

        One transaction for both. A company nobody can sign into is not a
        company, and a half-finished one is worse than neither: the name is
        taken, the slug is taken, and the only fix is a database edit.

        The email is checked first because it is globally unique across every
        company — the more useful failure is "that address already has an
        account" before anything is created, rather than an integrity error
        after the company row exists.
        """
        if await self.users.email_exists(admin_email):
            raise ConflictError(
                f"{admin_email} already has an account. One email belongs to "
                "one company.",
                code="EMAIL_ALREADY_REGISTERED",
            )

        slug = unique_slug(name, await self.companies.taken_slugs())
        company = await self.companies.create(name=name, slug=slug)

        admin = await self.users.create(
            company_id=company.id,
            email=admin_email,
            password_hash=hash_password(admin_password),
            full_name=admin_full_name,
            # An ADMIN of that company, never a second platform owner. The
            # platform role is not something this endpoint can grant.
            role=UserRole.ADMIN,
            is_active=True,
            # Created by the platform owner, who knows who this is.
            is_verified=True,
        )

        await self.db.commit()
        await self.db.refresh(company)
        await self.db.refresh(admin)

        logger.info(
            "Company created: %s (%s) with administrator %s",
            company.name,
            company.slug,
            admin.id,
        )
        return company, admin

    async def set_company_active(
        self, *, company_id: uuid.UUID, is_active: bool
    ) -> Company:
        """Suspend a company, or bring it back.

        Suspension is the only "removal" there is. Invoices and bills are
        accounting records, and a delete would cascade the audit trail with
        them — so this switches every account in the company off at once and
        leaves everything intact.

        It takes effect on the very next request: `get_current_active_user`
        checks the company alongside the account, so nobody keeps working until
        their access token expires.
        """
        company = await self.get_company(company_id)
        if company.is_active == is_active:
            return company

        await self.companies.set_active(company, is_active=is_active)
        await self.db.commit()
        await self.db.refresh(company)

        logger.warning(
            "Company %s (%s) %s",
            company.name,
            company.slug,
            "restored" if is_active else "SUSPENDED",
        )
        return company
