"""Company and Odoo-connection schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class CompanyRead(BaseModel):
    """A company as the API reports it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: dt.datetime


class OdooConfigStatus(BaseModel):
    """What is configured, and never the credential itself.

    There is deliberately no field here that could carry the API key back out.
    A settings screen needs to know whether a connection exists, where it
    points and whether it has ever worked — none of which requires showing the
    key, and showing it would put every company's ERP password one XSS away.
    """

    configured: bool
    base_url: str | None = None
    database: str | None = None
    username: str | None = None
    is_enabled: bool = False
    #: Null until the credentials have actually authenticated. "Saved" and
    #: "working" are different states and a settings screen should say which.
    verified_at: dt.datetime | None = None
    #: False when the server has no encryption key, in which case credentials
    #: can be neither saved nor read and the screen should say so rather than
    #: failing when somebody presses save.
    encryption_available: bool = True
    #: True when this company has no configuration of its own and is running on
    #: the server's environment fallback.
    using_server_fallback: bool = False


class OdooConfigWrite(BaseModel):
    """Credentials an administrator is saving for their own company.

    No `company_id`: the company comes from the caller's session, so there is
    no field with which to configure somebody else's Odoo.
    """

    base_url: str = Field(min_length=1, max_length=255)
    database: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=255)
    #: Write-only by construction — it appears in no response model anywhere.
    api_key: str = Field(min_length=1, max_length=512)
    is_enabled: bool = True
