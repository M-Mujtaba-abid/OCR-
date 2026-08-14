"""User model — database representation only, no behaviour."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth_session import AuthSession


class UserRole(str, enum.Enum):
    """Roles, ordered least to most privileged.

    Deliberately small. A three-role enum covers what this application needs;
    a full role/permission join table can replace it later without touching
    the dependency API in `app/dependencies/auth.py`, which is written against
    permissions rather than roles.
    """

    MEMBER = "member"
    MANAGER = "manager"
    ADMIN = "admin"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_is_active", "is_active"),
        # The admin queue filters by role constantly ("show me all members").
        Index("ix_users_role", "role"),
    )

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

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        # Never eager-load: a user with 50 devices would drag 50 rows into
        # every authenticated request. Sessions are loaded explicitly by the
        # repository when they are actually needed.
        lazy="raise",
    )
