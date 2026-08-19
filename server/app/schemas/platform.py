"""Platform-console schemas — the super admin's view of the companies.

Everything here is deliberately ABOUT companies and never about what is inside
one. There is no invoice, no vendor, no bill and no amount in this file, and
that absence is the design: the platform owner creates companies and their
first administrators, and has no business in anybody's payables.

The one count that appears — how many users a company has — is billing-shaped
rather than commercial: it says how big a tenant is without saying anything
about what they buy or from whom.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PlatformCompany(BaseModel):
    """One company as the platform console lists it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: dt.datetime

    #: How many accounts exist in this company, and how many can sign in.
    #: A company with zero active users is one nobody can get into, which is
    #: the state worth spotting from a list.
    user_count: int = 0
    active_user_count: int = 0
    admin_count: int = 0

    #: Whether this company has its own Odoo configured. Never any detail of
    #: it — the platform owner sets a company up, and the company's own
    #: administrator owns its credentials.
    odoo_configured: bool = False


class CompanyCreate(BaseModel):
    """A new company, and the administrator who will run it.

    Both together, in one request, on purpose. A company with no way to sign
    into it is not usable, and creating the two separately leaves a window
    where somebody has to remember to finish the job.

    No slug field: it is derived from the name, because it becomes an
    object-storage path segment and letting it be typed invites a value that
    collides with, or traverses out of, another company's prefix.
    """

    name: str = Field(min_length=1, max_length=160)

    admin_email: EmailStr
    admin_password: str = Field(
        min_length=8,
        max_length=128,
        description=(
            "The first administrator's password. The platform owner passes it "
            "on; there is no invitation email in this system."
        ),
    )
    admin_full_name: str | None = Field(default=None, max_length=255)


class CompanyCreated(BaseModel):
    """What was created. Never the password that was sent in."""

    company: PlatformCompany
    admin_email: EmailStr
    admin_id: uuid.UUID


class PlatformStats(BaseModel):
    """Headline counts for the console. Companies and accounts only."""

    companies: int
    active_companies: int
    users: int
