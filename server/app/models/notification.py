"""Notification — a status change addressed to one user.

Written by the services that change invoice state, read by the bell icon. One
row per recipient rather than one row per event with a join table: the query
that runs constantly is "my unread count", and that should touch a single
index, not a join.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.company import CompanyScopedMixin

if TYPE_CHECKING:
    from app.models.user import User


class NotificationType(str, enum.Enum):
    INVOICE_UPLOADED = "invoice_uploaded"
    PROCESSING_STARTED = "processing_started"
    OCR_COMPLETED = "ocr_completed"
    OCR_FAILED = "ocr_failed"
    MATCH_FOUND = "match_found"
    NO_MATCH_FOUND = "no_match_found"
    INVOICE_CONFIRMED = "invoice_confirmed"
    INVOICE_CORRECTED = "invoice_corrected"
    INVOICE_REJECTED = "invoice_rejected"
    INVOICE_PUSHED = "invoice_pushed"


class Notification(UUIDPrimaryKeyMixin, CompanyScopedMixin, Base):
    """No TimestampMixin: a notification is an immutable event. It is created
    and later marked read; `read_at` records that, so an `updated_at` would
    carry no information the row does not already have."""

    __tablename__ = "notifications"
    __table_args__ = (
        # The hot path — "unread for this user" — is served entirely by this
        # composite index without touching the table.
        Index("ix_notif_user_unread", "user_id", "is_read"),
        Index("ix_notif_match_history", "match_history_id"),
        Index("ix_notif_created", "created_at"),
        Index("ix_notifications_company_id", "company_id"),
    )

    # CASCADE here, unlike match_history: a notification about a deleted user
    # has no recipient and therefore no meaning.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    type: Mapped[NotificationType] = mapped_column(
        Enum(
            NotificationType,
            name="notification_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)

    match_history_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("match_history.id", ondelete="SET NULL")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="SET NULL")
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(lazy="raise")
