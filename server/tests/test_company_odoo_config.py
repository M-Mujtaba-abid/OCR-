"""A company's own Odoo credentials.

Two properties matter more than the CRUD does:

  * the API key goes in and never comes back out, through any route
  * a company configures its own Odoo and no other company's — the company is
    taken from the session, and there is no field with which to name another

The credentials used here point at a host that does not exist. Nothing in this
file connects to an Odoo; saving and reading configuration is all local.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secrets
from app.core.security import hash_password
from app.models.company import Company
from app.models.user import User, UserRole
from app.repositories.company_odoo_config_repository import (
    CompanyOdooConfigRepository,
)
from app.repositories.user_repository import UserRepository
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/company"
SECRET_KEY = "super-secret-odoo-api-key"

CONFIG = {
    "base_url": "https://nowhere.odoo.invalid",
    "database": "nowhere_db",
    "username": "someone@example.com",
    "api_key": SECRET_KEY,
}


async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


async def test_saving_credentials_never_returns_them(
    client: AsyncClient, admin_user: User, password: str
) -> None:
    """The response model has no field that could carry the key. This asserts
    it against the raw body, because "no field" is only true until somebody
    adds one."""
    headers = await _token(client, admin_user, password)

    r = await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

    assert r.status_code == 200, r.text
    assert SECRET_KEY not in r.text
    data = r.json()["data"]
    assert data["configured"] is True
    assert data["database"] == "nowhere_db"
    # Saved is not the same as working, and the screen should be able to say so.
    assert data["verified_at"] is None


async def test_reading_the_status_never_returns_the_key(
    client: AsyncClient, admin_user: User, password: str
) -> None:
    headers = await _token(client, admin_user, password)
    await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

    r = await client.get(f"{COMPANY}/odoo", headers=headers)

    assert r.status_code == 200, r.text
    assert SECRET_KEY not in r.text


async def test_the_key_is_encrypted_at_rest(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """A database dump must not hand over a company's ERP login."""
    headers = await _token(client, admin_user, password)
    await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

    assert admin_user.company_id is not None
    stored = await CompanyOdooConfigRepository(db).find_for_company(
        admin_user.company_id
    )

    assert stored is not None
    assert stored.api_key_encrypted != SECRET_KEY
    assert secrets.looks_like_fernet(stored.api_key_encrypted)
    # And it is genuinely recoverable, or the connection could never be made.
    assert secrets.decrypt_secret(stored.api_key_encrypted) == SECRET_KEY


async def test_a_member_cannot_configure_odoo(
    client: AsyncClient, existing_user: User, password: str
) -> None:
    headers = await _token(client, existing_user, password)

    r = await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

    assert r.status_code == 403, r.text


async def test_configuration_lands_on_the_callers_own_company(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """There is no company field in the request, so a body cannot redirect it."""
    other = Company(name="Rivals", slug=f"rival-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.commit()

    headers = await _token(client, admin_user, password)
    await client.put(
        f"{COMPANY}/odoo",
        headers=headers,
        json={**CONFIG, "company_id": str(other.id)},
    )

    repo = CompanyOdooConfigRepository(db)
    assert admin_user.company_id is not None
    assert await repo.find_for_company(admin_user.company_id) is not None
    assert await repo.find_for_company(other.id) is None


async def test_saving_twice_replaces_rather_than_duplicates(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """One row per company — the unique constraint says so, and an upsert is
    what keeps a second save from hitting it."""
    headers = await _token(client, admin_user, password)
    await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

    r = await client.put(
        f"{COMPANY}/odoo",
        headers=headers,
        json={**CONFIG, "database": "changed_db"},
    )

    assert r.status_code == 200, r.text
    assert admin_user.company_id is not None
    stored = await CompanyOdooConfigRepository(db).find_for_company(
        admin_user.company_id
    )
    assert stored is not None
    assert stored.database == "changed_db"


async def test_a_company_with_no_configuration_says_so_plainly(
    client: AsyncClient, admin_user: User, password: str
) -> None:
    """No configuration means no Odoo — not "the server's one".

    The fallback that used to sit here pointed every unconfigured company at
    whichever Odoo the deployment was built against, which on a platform
    running several tenants meant one company matching against another's
    purchase orders.
    """
    headers = await _token(client, admin_user, password)

    r = await client.get(f"{COMPANY}/odoo", headers=headers)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["configured"] is False
    assert "using_server_fallback" not in data


async def test_everybody_can_see_which_company_they_are_in(
    client: AsyncClient, existing_user: User, password: str
) -> None:
    """No permission beyond being signed in — the header renders this."""
    headers = await _token(client, existing_user, password)

    r = await client.get(COMPANY, headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["id"] == str(existing_user.company_id)


async def test_a_suspended_company_can_do_nothing(
    client: AsyncClient, db: AsyncSession, password: str
) -> None:
    """One flag stops every account in a company at once, on the next request
    rather than whenever a token happens to expire."""
    suspended = Company(name="Gone", slug=f"gone-{uuid.uuid4().hex[:8]}")
    db.add(suspended)
    await db.flush()
    member = await UserRepository(db).create(
        company_id=suspended.id,
        email=f"gone-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        role=UserRole.MEMBER,
    )
    await db.commit()

    headers = await _token(client, member, password)
    suspended.is_active = False
    await db.commit()

    r = await client.get("/api/v1/invoices/my", headers=headers)

    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "COMPANY_SUSPENDED"


class TestSwitchingAndRemoving:
    """The lifecycle after the credentials are in: off, on, and gone."""

    async def test_a_connection_can_be_switched_off_and_back_on(
        self, client: AsyncClient, admin_user: User, password: str
    ) -> None:
        """Both directions. Off without on is not a switch — the way back would
        be re-saving, which means retyping a key the API cannot show."""
        headers = await _token(client, admin_user, password)
        await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

        off = await client.post(f"{COMPANY}/odoo/disable", headers=headers)
        assert off.status_code == 200, off.text
        assert off.json()["data"]["is_enabled"] is False
        # Switched off is not the same as forgotten: the credentials remain.
        assert off.json()["data"]["configured"] is True

        on = await client.post(f"{COMPANY}/odoo/enable", headers=headers)
        assert on.status_code == 200, on.text
        assert on.json()["data"]["is_enabled"] is True

    async def test_a_switched_off_connection_refuses_to_resolve(
        self, client: AsyncClient, db: AsyncSession, admin_user: User, password: str
    ) -> None:
        """The flag has to reach the credential resolver, or the switch changes
        the screen and nothing else."""
        from app.core.exceptions import OdooNotConfiguredError
        from app.services.odoo_service import resolve_credentials

        headers = await _token(client, admin_user, password)
        await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)
        await client.post(f"{COMPANY}/odoo/disable", headers=headers)

        assert admin_user.company_id is not None
        with pytest.raises(OdooNotConfiguredError):
            await resolve_credentials(db, admin_user.company_id)

    async def test_switching_something_unconfigured_is_refused(
        self, client: AsyncClient, admin_user: User, password: str
    ) -> None:
        headers = await _token(client, admin_user, password)
        r = await client.post(f"{COMPANY}/odoo/enable", headers=headers)
        assert r.status_code in (400, 409, 503), r.text

    async def test_deleting_removes_the_credentials_entirely(
        self, client: AsyncClient, db: AsyncSession, admin_user: User, password: str
    ) -> None:
        """Different from disabling: the key is gone, not kept for later."""
        headers = await _token(client, admin_user, password)
        await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

        r = await client.delete(f"{COMPANY}/odoo", headers=headers)

        assert r.status_code == 200, r.text
        assert r.json()["data"]["configured"] is False
        assert admin_user.company_id is not None
        assert (
            await CompanyOdooConfigRepository(db).find_for_company(
                admin_user.company_id
            )
            is None
        )

    async def test_a_member_can_neither_switch_nor_delete(
        self, client: AsyncClient, existing_user: User, password: str
    ) -> None:
        headers = await _token(client, existing_user, password)
        assert (
            await client.post(f"{COMPANY}/odoo/enable", headers=headers)
        ).status_code == 403
        assert (
            await client.delete(f"{COMPANY}/odoo", headers=headers)
        ).status_code == 403


class TestNoFallback:
    """The hole this change closed."""

    async def test_a_company_without_odoo_is_refused_not_redirected(
        self, db: AsyncSession, admin_user: User
    ) -> None:
        """THE test for this change.

        Before, a company with no configuration silently resolved to whichever
        Odoo the server was deployed against — so a second tenant matched
        against the first's purchase orders and could raise bills in their
        ledger. Now it refuses, and the message names the fix.
        """
        from app.core.exceptions import OdooNotConfiguredError
        from app.services.odoo_service import resolve_credentials

        assert admin_user.company_id is not None
        with pytest.raises(OdooNotConfiguredError) as raised:
            await resolve_credentials(db, admin_user.company_id)

        assert "no Odoo connected" in str(raised.value.message)

    async def test_two_companies_on_one_database_are_flagged(
        self, client: AsyncClient, db: AsyncSession, admin_user: User, password: str
    ) -> None:
        """A warning, not a block — a group may share one Odoo — but two
        tenants unknowingly sharing a ledger is what this feature prevents."""
        headers = await _token(client, admin_user, password)
        await client.put(f"{COMPANY}/odoo", headers=headers, json=CONFIG)

        alone = await client.get(f"{COMPANY}/odoo", headers=headers)
        assert alone.json()["data"]["shared_with_another_company"] is False

        # A second company pointed at the same host and database.
        rival = Company(name="Rivals", slug=f"rival-{uuid.uuid4().hex[:8]}")
        db.add(rival)
        await db.flush()
        await CompanyOdooConfigRepository(db).upsert(
            company_id=rival.id,
            base_url=CONFIG["base_url"],
            database=CONFIG["database"],
            username="someone-else@example.com",
            api_key_encrypted=secrets.encrypt_secret("another-key"),
        )
        await db.commit()

        shared = await client.get(f"{COMPANY}/odoo", headers=headers)
        assert shared.json()["data"]["shared_with_another_company"] is True
