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


async def test_a_company_with_no_configuration_reports_the_fallback(
    client: AsyncClient, admin_user: User, password: str
) -> None:
    """"Not configured" and "running on the server's own Odoo" are different
    facts, and a settings screen that conflates them tells an administrator
    their company is disconnected when it is working."""
    headers = await _token(client, admin_user, password)

    r = await client.get(f"{COMPANY}/odoo", headers=headers)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["configured"] is False
    assert data["using_server_fallback"] is True


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
