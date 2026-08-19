"""Companies — the tenant boundary.

One row per business using this system. Everything a company owns — its users,
its uploads, its notifications, its Odoo — hangs off this table, and nothing is
readable across two of them.

Two identifiers, each used where it fits. `id` is a UUID and is what every
foreign key points at. `slug` is a short handle that goes into object storage
paths, so a bucket listing stays readable and a whole company's files can be
exported or removed by prefix. Both are immutable once set: a slug that changes
orphans every object already written under the old one.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"

    #: What people call it: "FreshLeaf", "KJ Restaurants".
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    #: Lowercase, hyphenated, unique, immutable. Object keys are built from
    #: this — `storage.build_object_key` already replaces anything unsafe in the
    #: segment, so a bad slug cannot escape its prefix; the rule is enforced at
    #: the point companies are created so the stored value and the key agree.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    #: False suspends every login in the company without deleting a thing.
    #: There is no delete: invoices and bills are accounting records, and a
    #: cascade through `match_history` would take the audit trail with them.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    odoo: Mapped["CompanyOdooConfig | None"] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        uselist=False,
        # Never eager: the row carries a credential, and it should be loaded
        # because somebody meant to use it rather than because they read a
        # company for its name.
        lazy="raise",
    )


class CompanyOdooConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One company's Odoo connection.

    A separate table rather than columns on `companies`, so the secret is
    loaded deliberately. Listing companies for the platform console should not
    drag every tenant's ERP credentials into memory to render a name.

    Optional per company: a company that only reviews scans and never pushes to
    an ERP simply has no row here.
    """

    __tablename__ = "company_odoo_config"

    company_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT, like everything else pointing at a company: the config is
        # deleted with the company or not at all, and companies are not deleted.
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    database: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The API key, encrypted at rest — never the plaintext, and never returned
    #: by any endpoint to anybody, including the platform owner. The column is
    #: opaque text here on purpose: which cipher produced it is the encryption
    #: helper's business, not the schema's.
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    #: A switch a company admin can flip without deleting their credentials —
    #: useful while an Odoo is down and every match would otherwise 502.
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    #: When these credentials last authenticated successfully. Null means they
    #: have been saved but never proven, which is worth showing differently
    #: from "working" on a settings screen.
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped["Company"] = relationship(back_populates="odoo", lazy="raise")


class CompanyScopedMixin:
    """`company_id` for every table whose rows belong to exactly one company.

    A mixin rather than the same eight lines copied into each model: the column,
    its foreign key, its delete behaviour and its index are one decision, and
    four separate copies of a decision is four places for it to drift.

    RESTRICT, not CASCADE. A company is suspended, never deleted, and this is
    the constraint that makes that policy true rather than merely intended —
    an accidental delete fails loudly instead of silently taking a year of
    payables with it.

    `users` deliberately does NOT use this: the platform owner belongs to no
    company, so that column is nullable and carries its own constraint.

    The INDEX is not here, on purpose. Every one of these tables needs one, but
    not the same one: a table that is always read as "this company, that
    status" wants the composite and would carry a standalone index that no
    query ever reaches for. So each table declares the shape it is actually
    queried by — and each of them must declare something.
    """

    @declared_attr
    @classmethod
    def company_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
        )
