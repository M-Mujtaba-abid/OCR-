"""The company boundary, tested where it is decided.

`company_of` is the single answer to "whose work is this", so every write into
a company-scoped table goes through it. These are pure — no database, no HTTP —
because the rule itself is pure, and a rule this load-bearing should be provable
against literals rather than against a fixture.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.tenancy import NoCompanyError, company_of
from app.models.user import User, UserRole


def _user(*, company_id: uuid.UUID | None, role: UserRole) -> User:
    """A User that was never persisted. Enough for a rule that reads two fields."""
    return User(
        company_id=company_id,
        email="someone@example.com",
        password_hash="x",
        role=role,
    )


def test_a_company_member_stamps_their_own_company() -> None:
    company = uuid.uuid4()
    assert company_of(_user(company_id=company, role=UserRole.MEMBER)) == company


@pytest.mark.parametrize(
    "role", [UserRole.MEMBER, UserRole.MANAGER, UserRole.ADMIN]
)
def test_every_in_company_role_resolves_the_same_way(role: UserRole) -> None:
    """A company admin is not more or less scoped than a member.

    The roles differ in what they may do inside a company, never in which
    company that is.
    """
    company = uuid.uuid4()
    assert company_of(_user(company_id=company, role=role)) == company


def test_the_platform_owner_has_no_company_to_write_into() -> None:
    """The super admin sits outside the companies, so this must fail.

    Null `company_id` must never degrade into "all companies". If this test
    ever starts returning a value instead of raising, the platform owner has
    silently become a member of whichever company the code picked.
    """
    with pytest.raises(NoCompanyError):
        company_of(_user(company_id=None, role=UserRole.SUPER_ADMIN))


def test_a_company_less_account_fails_whatever_its_role() -> None:
    """Belt and braces: the check is on the company, not on the role.

    A row that slipped past the database constraint — an older account, a bad
    manual insert — still must not write into a company it does not name.
    """
    with pytest.raises(NoCompanyError):
        company_of(_user(company_id=None, role=UserRole.ADMIN))


def test_the_error_is_a_403_not_a_500() -> None:
    """It is a refusal, not a crash: the caller asked for something they are
    not entitled to, and the response should say so."""
    assert NoCompanyError().status_code == 403
