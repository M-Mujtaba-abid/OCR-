"""Account creation, now that public sign-up is gone.

`POST /users` is the only way an account comes into existence, which makes the
rules here the whole of the account-creation security model rather than one
check among several:

  * the company comes from the ADMINISTRATOR, never from the request body
  * `super_admin` cannot be minted from inside a company, by creation or by
    promotion
  * a member cannot create anyone

The email-uniqueness and password-length cases moved here from the old
registration suite — the rules did not change, only who is allowed to invoke
them.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.company import Company
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

USERS = "/api/v1/users"


async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


async def test_admin_creates_a_user_in_their_own_company(
    client: AsyncClient, admin_user: User, password: str, unique_email: str
) -> None:
    headers = await _token(client, admin_user, password)

    r = await client.post(
        USERS,
        headers=headers,
        json={"email": unique_email, "password": password, "full_name": "New Member"},
    )

    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["email"] == unique_email
    # Least privilege by default, exactly as self-registration used to grant.
    assert data["role"] == "member"
    # Created by somebody who already knows who they are, so there is nothing
    # left for a verification email to establish.
    assert data["is_verified"] is True


async def test_the_new_user_lands_in_the_creator_s_company(
    client: AsyncClient,
    db: AsyncSession,
    admin_user: User,
    password: str,
    unique_email: str,
) -> None:
    """The company is taken from the session, and there is no field to say
    otherwise — a `company_id` in the body must not move the account."""
    headers = await _token(client, admin_user, password)
    somewhere_else = Company(name="Elsewhere", slug=f"else-{uuid.uuid4().hex[:8]}")
    db.add(somewhere_else)
    await db.commit()

    r = await client.post(
        USERS,
        headers=headers,
        json={
            "email": unique_email,
            "password": password,
            # Ignored: not part of the schema, and the service never reads it.
            "company_id": str(somewhere_else.id),
        },
    )

    assert r.status_code == 201, r.text
    created = await UserRepository(db).find_by_email(unique_email)
    assert created is not None
    assert created.company_id == admin_user.company_id
    assert created.company_id != somewhere_else.id


async def test_a_member_cannot_create_accounts(
    client: AsyncClient, existing_user: User, password: str, unique_email: str
) -> None:
    headers = await _token(client, existing_user, password)
    r = await client.post(
        USERS,
        headers=headers,
        json={"email": unique_email, "password": password},
    )
    assert r.status_code == 403, r.text


async def test_creating_accounts_requires_a_session(
    client: AsyncClient, unique_email: str, password: str
) -> None:
    r = await client.post(
        USERS, json={"email": unique_email, "password": password}
    )
    assert r.status_code == 401, r.text


async def test_the_platform_owner_cannot_be_minted_from_a_company(
    client: AsyncClient, admin_user: User, password: str, unique_email: str
) -> None:
    """A company admin reaching this endpoint must not be able to create an
    account that sits above every company."""
    headers = await _token(client, admin_user, password)
    r = await client.post(
        USERS,
        headers=headers,
        json={
            "email": unique_email,
            "password": password,
            "role": "super_admin",
        },
    )
    assert r.status_code == 403, r.text


async def test_promotion_is_not_a_back_door_to_the_platform_owner(
    client: AsyncClient, admin_user: User, existing_user: User, password: str
) -> None:
    """Same rule from the other side: if creation refuses the role, promoting
    an existing member into it must refuse too."""
    headers = await _token(client, admin_user, password)
    r = await client.patch(
        f"{USERS}/{existing_user.id}/role",
        headers=headers,
        json={"role": "super_admin"},
    )
    assert r.status_code == 403, r.text


async def test_duplicate_email_conflicts(
    client: AsyncClient, admin_user: User, existing_user: User, password: str
) -> None:
    headers = await _token(client, admin_user, password)
    r = await client.post(
        USERS,
        headers=headers,
        json={"email": existing_user.email, "password": password},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


async def test_duplicate_email_is_case_insensitive(
    client: AsyncClient, admin_user: User, existing_user: User, password: str
) -> None:
    headers = await _token(client, admin_user, password)
    r = await client.post(
        USERS,
        headers=headers,
        json={"email": existing_user.email.upper(), "password": password},
    )
    assert r.status_code == 409, r.text


async def test_short_passwords_are_refused(
    client: AsyncClient, admin_user: User, password: str, unique_email: str
) -> None:
    headers = await _token(client, admin_user, password)
    r = await client.post(
        USERS, headers=headers, json={"email": unique_email, "password": "short"}
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_the_directory_shows_only_your_own_company(
    client: AsyncClient,
    db: AsyncSession,
    admin_user: User,
    existing_user: User,
    password: str,
) -> None:
    """The listing is the screen an administrator manages people from. If it
    reaches across companies, so does everything reached from it."""
    other_company = Company(name="Rivals", slug=f"rival-{uuid.uuid4().hex[:8]}")
    db.add(other_company)
    await db.flush()
    outsider = await UserRepository(db).create(
        company_id=other_company.id,
        email=f"outsider-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    await db.commit()

    headers = await _token(client, admin_user, password)
    r = await client.get(f"{USERS}?page_size=100", headers=headers)

    assert r.status_code == 200, r.text
    emails = {row["email"] for row in r.json()["data"]["items"]}
    assert admin_user.email in emails
    assert existing_user.email in emails
    assert outsider.email not in emails


async def test_reading_a_user_from_another_company_is_a_404_not_a_403(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """A 403 would confirm the id exists, turning this into a way to probe
    another company's directory one id at a time."""
    other_company = Company(name="Rivals", slug=f"rival-{uuid.uuid4().hex[:8]}")
    db.add(other_company)
    await db.flush()
    outsider = await UserRepository(db).create(
        company_id=other_company.id,
        email=f"outsider-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        role=UserRole.MEMBER,
    )
    await db.commit()

    headers = await _token(client, admin_user, password)
    r = await client.get(f"{USERS}/{outsider.id}", headers=headers)
    assert r.status_code == 404, r.text
