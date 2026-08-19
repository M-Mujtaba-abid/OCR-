"""Company administration business rules.

Today this is one thing: a company's own Odoo connection. Everything here is
scoped to the company the caller belongs to — there is no method that takes a
company id from anywhere but the actor.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secrets
from app.core.exceptions import OdooError
from app.core.secrets import encrypt_secret
from app.core.tenancy import company_of
from app.lib.logging import get_logger
from app.models.user import User
from app.repositories.company_odoo_config_repository import (
    CompanyOdooConfigRepository,
)
from app.schemas.company import OdooConfigStatus
from app.services.odoo_service import (
    OdooCredentials,
    OdooService,
    reset_odoo_client,
)

logger = get_logger(__name__)


class CompanyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.configs = CompanyOdooConfigRepository(db)

    async def odoo_status(self, *, actor: User) -> OdooConfigStatus:
        """What this company's Odoo connection looks like, minus the secret."""
        company_id = company_of(actor)
        config = await self.configs.find_for_company(company_id)

        if config is None:
            return OdooConfigStatus(
                configured=False,
                encryption_available=secrets.is_configured(),
                # A company with no row of its own runs on whatever the server
                # has configured. Saying so is the difference between "not set
                # up" and "set up somewhere you cannot see".
                using_server_fallback=True,
            )

        return OdooConfigStatus(
            configured=True,
            base_url=config.base_url,
            database=config.database,
            username=config.username,
            is_enabled=config.is_enabled,
            verified_at=config.verified_at,
            encryption_available=secrets.is_configured(),
        )

    async def save_odoo_config(
        self,
        *,
        actor: User,
        base_url: str,
        database: str,
        username: str,
        api_key: str,
        is_enabled: bool,
    ) -> OdooConfigStatus:
        """Store this company's credentials, encrypted.

        The cached client and credentials are dropped for THIS company only, so
        the change takes effect on the next call without making every other
        company re-authenticate.
        """
        company_id = company_of(actor)

        config = await self.configs.upsert(
            company_id=company_id,
            base_url=base_url.strip().rstrip("/"),
            database=database.strip(),
            username=username.strip(),
            api_key_encrypted=encrypt_secret(api_key),
            is_enabled=is_enabled,
        )
        await self.db.commit()

        reset_odoo_client(company_id)
        logger.info(
            "Odoo configuration saved for company %s (%s)",
            company_id,
            config.base_url,
        )

        return await self.odoo_status(actor=actor)

    async def verify_odoo(self, *, actor: User) -> dict[str, object]:
        """Authenticate against this company's Odoo and record the result.

        Its own endpoint because four separate values have to be right — URL,
        database, username, key — and working out which one is wrong through a
        failing purchase-order fetch is miserable.
        """
        company_id = company_of(actor)
        config = await self.configs.find_for_company(company_id)
        if config is None:
            raise OdooError(
                "This company has no Odoo configuration to verify.",
                code="ODOO_NOT_CONFIGURED",
            )

        credentials = OdooCredentials(
            base_url=config.base_url,
            database=config.database,
            username=config.username,
            api_key=secrets.decrypt_secret(config.api_key_encrypted),
        )
        # A fresh service rather than a cached one: verifying is exactly when
        # the caller wants the credentials as they now stand, not as they were
        # when something last connected.
        reset_odoo_client(company_id)
        result = await OdooService(company_id, credentials).check_connection()

        await self.configs.mark_verified(config, at=dt.datetime.now(dt.UTC))
        await self.db.commit()
        return result

    async def disable_odoo(self, *, actor: User) -> OdooConfigStatus:
        """Switch the connection off without discarding the credentials.

        For an Odoo that is down or being migrated: every match and bill fails
        fast with a clear reason instead of timing out, and turning it back on
        does not mean re-entering an API key.
        """
        company_id = company_of(actor)
        config = await self.configs.find_for_company(company_id)
        if config is not None:
            config.is_enabled = False
            await self.db.commit()
            reset_odoo_client(company_id)
        return await self.odoo_status(actor=actor)

    @staticmethod
    def company_id_of(actor: User) -> uuid.UUID:
        return company_of(actor)
