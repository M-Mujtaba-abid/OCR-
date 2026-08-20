"""One company's Odoo connection settings. No business logic, no HTTP."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import CompanyOdooConfig


class CompanyOdooConfigRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def find_for_company(
        self, company_id: uuid.UUID
    ) -> CompanyOdooConfig | None:
        """The row, or None when this company has never configured Odoo.

        None is an ordinary answer, not an error: a company that only reviews
        scans and never pushes to an ERP has no row here. It is NOT a licence
        to substitute somebody else's connection — `resolve_credentials`
        refuses rather than falling back.
        """
        stmt = select(CompanyOdooConfig).where(
            CompanyOdooConfig.company_id == company_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        company_id: uuid.UUID,
        base_url: str,
        database: str,
        username: str,
        api_key_encrypted: str,
        is_enabled: bool = True,
    ) -> CompanyOdooConfig:
        """Create or replace this company's connection. Does NOT commit.

        One row per company is enforced by a unique constraint, so this reads
        first and updates in place rather than inserting a second.
        """
        config = await self.find_for_company(company_id)
        if config is None:
            config = CompanyOdooConfig(company_id=company_id)
            self.db.add(config)

        config.base_url = base_url
        config.database = database
        config.username = username
        config.api_key_encrypted = api_key_encrypted
        config.is_enabled = is_enabled
        # Credentials that have just changed are unproven again, whatever the
        # old ones did. `mark_verified` is what says otherwise.
        config.verified_at = None

        await self.db.flush()
        await self.db.refresh(config)
        return config

    async def is_shared(
        self, *, company_id: uuid.UUID, base_url: str, database: str
    ) -> bool:
        """Whether another company points at this same Odoo database.

        Not a constraint. A group of companies may genuinely share one Odoo
        with several `res.company` records inside it, and refusing that would
        block a legitimate setup. But two tenants unknowingly sharing a ledger
        is precisely what per-company credentials exist to prevent, so it is
        worth saying out loud on the settings screen.

        `EXISTS` rather than a count: the answer is a yes/no and the query can
        stop at the first row.
        """
        stmt = (
            select(CompanyOdooConfig.id)
            .where(
                CompanyOdooConfig.company_id != company_id,
                CompanyOdooConfig.base_url == base_url,
                CompanyOdooConfig.database == database,
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).first() is not None

    async def delete(self, config: CompanyOdooConfig) -> None:
        """Remove this company's connection entirely. Does NOT commit.

        Nothing cascades: invoices keep their `odoo_bill_id` and the audit blob
        in `extra`, because those record what this system DID rather than what
        Odoo currently holds. A company can drop its ERP and keep its history.
        """
        await self.db.delete(config)
        await self.db.flush()

    async def mark_verified(
        self, config: CompanyOdooConfig, *, at: dt.datetime
    ) -> CompanyOdooConfig:
        """Record that these credentials authenticated. Does NOT commit."""
        config.verified_at = at
        await self.db.flush()
        await self.db.refresh(config)
        return config
