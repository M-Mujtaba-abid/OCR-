"""User model — database representation only, no behaviour."""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth_session import AuthSession
    from app.models.company import Company


class UserRole(str, enum.Enum):
    """Roles, ordered least to most privileged.

    Deliberately small. A four-role enum covers what this application needs;
    a full role/permission join table can replace it later without touching
    the dependency API in `app/dependencies/auth.py`, which is written against
    permissions rather than roles.

    The first three are roles WITHIN a company and mean the same thing in every
    one of them. `SUPER_ADMIN` is not a fourth rung on that ladder — it is
    outside the companies entirely, which is why it is the one role whose
    holder has no `company_id`.
    """

    MEMBER = "member"
    MANAGER = "manager"
    ADMIN = "admin"
    #: The platform owner. Creates companies and each company's first
    #: administrator, and suspends a company. Holds NO permission over any
    #: company's invoices, bills or notifications — see `ROLE_PERMISSIONS`,
    #: where an empty grant is the point rather than an omission.
    SUPER_ADMIN = "super_admin"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_is_active", "is_active"),
        # The admin queue filters by role constantly ("show me all members").
        Index("ix_users_role", "role"),
        # Every company's user list is "these users, that company".
        Index("ix_users_company_role", "company_id", "role"),
        # The nullable column above is nullable for exactly one reason, and
        # this is that reason written down where the database can enforce it.
        # Without it, "company_id is null" degrades from "the platform owner"
        # to "somebody forgot", and a user with no company is a user no
        # company-scoped query will ever return.
        #
        # `role::text`, not a bare `role = 'super_admin'`. Alembic runs a whole
        # upgrade in ONE transaction, and Postgres refuses to use an enum label
        # in the same transaction that added it — so comparing the enum against
        # its own new label would fail the very deploy that introduces it.
        # Casting to text compares two strings and never names an enum value.
        CheckConstraint(
            "(company_id IS NOT NULL) OR (role::text = 'super_admin')",
            name="user_belongs_to_company",
        ),
    )

    # Which company this person works for.
    #
    # NOT the `CompanyScopedMixin` every other table uses, and nullable where
    # those are not: the platform owner sits outside the companies rather than
    # in one. Null therefore means "no company", never "every company" — a
    # scoped query must return this user nothing at all. The check constraint
    # above is what keeps null meaning only that.
    #
    # RESTRICT matches the mixin: companies are suspended, never deleted.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT")
    )

    # Globally unique, deliberately, not unique-per-company. One email is one
    # person is one company, so login stays a plain email and password with no
    # company to pick first. Letting one address belong to two companies means
    # a memberships table and a chooser on the way in; it is a real product
    # decision, and this is the constraint that records which way it went.
    #
    # Always stored lowercased by UserRepository, so a plain unique constraint
    # is enough — no functional index, which Alembic cannot autogenerate.
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )

    # Named password_hash, never `password`. The name is the documentation:
    # nobody reads `user.password_hash` and thinks it holds a plaintext value.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[str | None] = mapped_column(String(255))

    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            # Without values_callable SQLAlchemy persists the member NAME
            # ("ADMIN") rather than its value ("admin"), which then mismatches
            # the API contract at runtime.
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=UserRole.MEMBER,
        server_default=UserRole.MEMBER.value,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # A member is usually a vendor. These cache which Odoo res.partner they
    # are, so an uploaded invoice can be attributed without a lookup on every
    # request. Plain integers, not FKs — that record lives in Odoo.
    odoo_partner_id: Mapped[int | None] = mapped_column(Integer)
    odoo_partner_name: Mapped[str | None] = mapped_column(String(255))

    company: Mapped["Company | None"] = relationship(lazy="raise")

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        # Never eager-load: a user with 50 devices would drag 50 rows into
        # every authenticated request. Sessions are loaded explicitly by the
        # repository when they are actually needed.
        lazy="raise",
    )
