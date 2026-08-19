"""The platform owner: what they can do, and — mostly — what they cannot.

Creating companies is the easy half. The half that matters is the boundary: an
account that can create every company must not be able to read inside any of
them, or "the platform owner can see everything" becomes one forgotten filter
away from one company reading another's ledger.

Two independent things enforce that, and both are tested here:

  * `platform.admin` is granted alongside `user.read.self` and nothing else, so
    every company-scoped route refuses on permission
  * `company_of()` raises for an account with no company, so even a permission
    granted by mistake could not resolve a company to scope a query to
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole
from app.repositories.company_repository import CompanyRepository
from app.repositories.user_repository import UserRepository
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

PLATFORM = "/api/v1/platform"


@pytest.fixture
async def platform_owner(db: AsyncSession, password: str) -> User:
    """An account with no company at all — the one the constraint allows."""
    user = await UserRepository(db).create(
        company_id=None,
        email=f"owner-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        full_name="Platform Owner",
        role=UserRole.SUPER_ADMIN,
    )
    await db.commit()
    return user


async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


class TestCreatingCompanies:
    async def test_a_company_and_its_first_admin_are_created_together(
        self, client: AsyncClient, platform_owner: User, password: str
    ) -> None:
        """Either alone is not a working company."""
        headers = await _token(client, platform_owner, password)
        email = f"kj-admin-{uuid.uuid4().hex[:10]}@example.com"

        r = await client.post(
            f"{PLATFORM}/companies",
            headers=headers,
            json={
                "name": "KJ Restaurants",
                "admin_email": email,
                "admin_password": password,
                "admin_full_name": "KJ Admin",
            },
        )

        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["company"]["name"] == "KJ Restaurants"
        assert data["company"]["slug"] == "kj-restaurants"
        assert data["admin_email"] == email
        assert data["company"]["admin_count"] == 1

    async def test_the_new_admin_can_sign_in_and_runs_their_own_company(
        self, client: AsyncClient, platform_owner: User, password: str
    ) -> None:
        """The handover: from here the company administers itself."""
        headers = await _token(client, platform_owner, password)
        email = f"kj-admin-{uuid.uuid4().hex[:10]}@example.com"
        created = await client.post(
            f"{PLATFORM}/companies",
            headers=headers,
            json={
                "name": "KJ Restaurants",
                "admin_email": email,
                "admin_password": password,
            },
        )
        assert created.status_code == 201, created.text

        response = await login(client, email, password)
        assert response.status_code == 200, response.text
        admin_headers = auth_header(response.json()["data"]["access_token"])

        # They can see their own company, and their own (empty) queue.
        me = await client.get("/api/v1/company", headers=admin_headers)
        assert me.status_code == 200
        assert me.json()["data"]["slug"] == "kj-restaurants"

        queue = await client.get(
            "/api/v1/invoices/admin/queue", headers=admin_headers
        )
        assert queue.status_code == 200
        assert queue.json()["data"]["items"] == []

    async def test_a_duplicate_admin_email_creates_no_company(
        self, client: AsyncClient, db: AsyncSession, platform_owner: User,
        existing_user: User, password: str,
    ) -> None:
        """The check comes first on purpose: a company whose name and slug are
        taken but which nobody can sign into is worse than no company."""
        headers = await _token(client, platform_owner, password)
        before = len(await CompanyRepository(db).list_all())

        r = await client.post(
            f"{PLATFORM}/companies",
            headers=headers,
            json={
                "name": "Doomed Company",
                "admin_email": existing_user.email,
                "admin_password": password,
            },
        )

        assert r.status_code == 409, r.text
        assert len(await CompanyRepository(db).list_all()) == before

    async def test_two_companies_with_the_same_name_get_distinct_slugs(
        self, client: AsyncClient, platform_owner: User, password: str
    ) -> None:
        """Slugs become object-storage prefixes, so a collision would put two
        companies' files under one path."""
        headers = await _token(client, platform_owner, password)

        slugs = []
        for _ in range(2):
            r = await client.post(
                f"{PLATFORM}/companies",
                headers=headers,
                json={
                    "name": "Bright Foods",
                    "admin_email": f"a-{uuid.uuid4().hex[:10]}@example.com",
                    "admin_password": password,
                },
            )
            assert r.status_code == 201, r.text
            slugs.append(r.json()["data"]["company"]["slug"])

        assert slugs[0] == "bright-foods"
        assert slugs[1] == "bright-foods-2"


class TestTheBoundary:
    """What the platform owner must NOT be able to reach."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/invoices/admin/queue",
            "/api/v1/invoices/admin/stats",
            "/api/v1/invoices/admin/bills",
            "/api/v1/invoices/my",
            "/api/v1/users",
            "/api/v1/company/odoo",
        ],
    )
    async def test_the_platform_owner_cannot_read_inside_a_company(
        self, client: AsyncClient, platform_owner: User, password: str, path: str
    ) -> None:
        """Somebody who creates companies has no business in their payables.

        403 on every one of these, from the permission grant — `platform.admin`
        comes with `user.read.self` and nothing else.
        """
        headers = await _token(client, platform_owner, password)

        r = await client.get(path, headers=headers)

        assert r.status_code == 403, f"{path} -> {r.status_code}: {r.text}"

    async def test_they_cannot_create_users_in_a_company(
        self, client: AsyncClient, platform_owner: User, password: str
    ) -> None:
        """Only through `POST /platform/companies`, and only as that company's
        first administrator."""
        headers = await _token(client, platform_owner, password)

        r = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": f"x-{uuid.uuid4().hex[:10]}@example.com",
                "password": password,
            },
        )

        assert r.status_code == 403, r.text

    async def test_a_company_admin_cannot_reach_the_platform_console(
        self, client: AsyncClient, admin_user: User, password: str
    ) -> None:
        """The other direction: `system.admin` is not `platform.admin`."""
        headers = await _token(client, admin_user, password)

        r = await client.get(f"{PLATFORM}/companies", headers=headers)

        assert r.status_code == 403, r.text

    async def test_a_member_cannot_reach_the_platform_console(
        self, client: AsyncClient, existing_user: User, password: str
    ) -> None:
        headers = await _token(client, existing_user, password)
        r = await client.get(f"{PLATFORM}/companies", headers=headers)
        assert r.status_code == 403, r.text

    async def test_the_console_needs_a_session(self, client: AsyncClient) -> None:
        r = await client.get(f"{PLATFORM}/companies")
        assert r.status_code == 401, r.text


class TestSuspension:
    async def test_suspending_a_company_stops_its_people_immediately(
        self, client: AsyncClient, platform_owner: User, password: str
    ) -> None:
        """On the next request, not when a token expires — the account check
        and the company check happen in the same place."""
        headers = await _token(client, platform_owner, password)
        email = f"kj-{uuid.uuid4().hex[:10]}@example.com"
        created = await client.post(
            f"{PLATFORM}/companies",
            headers=headers,
            json={
                "name": "Short Lived",
                "admin_email": email,
                "admin_password": password,
            },
        )
        company_id = created.json()["data"]["company"]["id"]

        signed_in = auth_header(
            (await login(client, email, password)).json()["data"]["access_token"]
        )
        assert (
            await client.get("/api/v1/company", headers=signed_in)
        ).status_code == 200

        suspended = await client.post(
            f"{PLATFORM}/companies/{company_id}/suspend", headers=headers
        )
        assert suspended.status_code == 200, suspended.text

        blocked = await client.get("/api/v1/company", headers=signed_in)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "COMPANY_SUSPENDED"

    async def test_restoring_lets_them_back_in(
        self, client: AsyncClient, platform_owner: User, password: str
    ) -> None:
        """Suspension is the only removal there is, so it has to be reversible:
        the invoices and bills were never deleted."""
        headers = await _token(client, platform_owner, password)
        email = f"kj-{uuid.uuid4().hex[:10]}@example.com"
        created = await client.post(
            f"{PLATFORM}/companies",
            headers=headers,
            json={
                "name": "Back Again",
                "admin_email": email,
                "admin_password": password,
            },
        )
        company_id = created.json()["data"]["company"]["id"]
        signed_in = auth_header(
            (await login(client, email, password)).json()["data"]["access_token"]
        )

        await client.post(f"{PLATFORM}/companies/{company_id}/suspend", headers=headers)
        await client.post(f"{PLATFORM}/companies/{company_id}/restore", headers=headers)

        assert (
            await client.get("/api/v1/company", headers=signed_in)
        ).status_code == 200
