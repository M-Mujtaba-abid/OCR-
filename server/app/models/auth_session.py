"""Refresh-token session — one row per logged-in device."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class AuthSession(UUIDPrimaryKeyMixin, Base):
    """A single refresh-token session.

    One row per device, so a user can be signed in on several at once and
    revoke them individually. The raw refresh token is NEVER stored — only its
    SHA-256 digest, so a database leak yields no usable sessions.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
        # Serves the "list/revoke a user's live sessions" query directly.
        Index("ix_auth_sessions_user_revoked", "user_id", "revoked_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # SHA-256 hex digest = exactly 64 chars. Unique because two sessions can
    # never legitimately share a token, and the constraint turns a collision
    # into a loud database error instead of a silent security hole.
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Not TimestampMixin: a session row is immutable apart from its revocation
    # and last-used stamps, so an auto-updating `updated_at` would be noise.
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Points at the session that replaced this one during rotation.
    #
    # This column is what makes theft detection possible. "Revoked" alone
    # cannot distinguish a token replaced by rotation from one revoked by
    # logout, so reuse of a stolen token would look identical to a stray
    # request from a signed-out client. If a presented token maps to a session
    # that was revoked AND has rotated_to_id set, the legitimate client should
    # already be holding the successor — so someone else has this one.
    rotated_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL")
    )

    # 45 chars is the maximum length of an IPv6 address in text form.
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    user: Mapped["User"] = relationship(back_populates="sessions", lazy="joined")

    def is_active(self, now: dt.datetime | None = None) -> bool:
        """Usable right now: not revoked and not expired."""
        now = now or dt.datetime.now(dt.UTC)
        return self.revoked_at is None and self.expires_at > now

    @property
    def was_rotated(self) -> bool:
        return self.rotated_to_id is not None
