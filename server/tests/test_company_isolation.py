"""Two companies must not see each other.

The single most dangerous failure this system can have is not a crash — it is
one business quietly reading another's payables. These tests create a second
company and assert the boundary holds, because "we were careful" is not a
property anything can verify later.

Everything here rolls back with the test's transaction, so the second company
never outlives the assertion that needed it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.company import Company
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository

pytestmark = pytest.mark.asyncio


async def _company(db: AsyncSession, label: str) -> Company:
    company = Company(name=label, slug=f"{label.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(company)
    await db.flush()
    return company


async def _admin(db: AsyncSession, company: Company) -> User:
    return await UserRepository(db).create(
        company_id=company.id,
        email=f"admin-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password("CorrectHorseBattery1"),
        role=UserRole.ADMIN,
    )


async def test_admin_fanout_stops_at_the_company_boundary(db: AsyncSession) -> None:
    """The notification fan-out is the classic cross-tenant leak.

    Notification titles carry file names and vendor names, so an unscoped
    "notify every admin" does not merely reach the wrong inbox — it tells one
    business what another is buying.
    """
    freshleaf = await _company(db, "Freshleaf")
    kj = await _company(db, "KJ")
    freshleaf_admin = await _admin(db, freshleaf)
    kj_admin = await _admin(db, kj)

    repo = UserRepository(db)
    freshleaf_admins = await repo.list_ids_by_role(
        UserRole.ADMIN, company_id=freshleaf.id
    )
    kj_admins = await repo.list_ids_by_role(UserRole.ADMIN, company_id=kj.id)

    assert freshleaf_admin.id in freshleaf_admins
    assert kj_admin.id not in freshleaf_admins

    assert kj_admin.id in kj_admins
    assert freshleaf_admin.id not in kj_admins


async def test_listing_admins_requires_naming_a_company(db: AsyncSession) -> None:
    """`company_id` is keyword-only with no default, so "every admin
    everywhere" is not something a caller can ask for by forgetting."""
    with pytest.raises(TypeError):
        await UserRepository(db).list_ids_by_role(UserRole.ADMIN)  # type: ignore[call-arg]


async def test_a_user_cannot_be_created_without_a_company(db: AsyncSession) -> None:
    """The database, not the application, is what holds this line.

    A company-less account is invisible to every scoped query, so it is a
    broken account rather than a harmless one — and the check constraint means
    it cannot be written at all, whatever path tries.
    """
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await UserRepository(db).create(
            company_id=None,
            email=f"orphan-{uuid.uuid4().hex[:12]}@example.com",
            password_hash=hash_password("CorrectHorseBattery1"),
            role=UserRole.MEMBER,
        )


async def test_the_platform_owner_is_the_one_account_without_a_company(
    db: AsyncSession,
) -> None:
    """Same constraint, from the other side: super_admin is exempt by name."""
    owner = await UserRepository(db).create(
        company_id=None,
        email=f"owner-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password("CorrectHorseBattery1"),
        role=UserRole.SUPER_ADMIN,
    )
    assert owner.company_id is None
    assert owner.role is UserRole.SUPER_ADMIN
