"""Processing batch — one admin "Start Process" click.

Groups 1..N uploaded invoices into a single run so progress can be reported per
batch instead of per invoice, and so a partial failure is visible as such.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.company import CompanyScopedMixin

if TYPE_CHECKING:
    from app.models.match_history import MatchHistory
    from app.models.user import User


class BatchStatus(str, enum.Enum):
    CREATED = "created"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class ProcessingBatch(UUIDPrimaryKeyMixin, CompanyScopedMixin, TimestampMixin, Base):
    __tablename__ = "processing_batches"
    __table_args__ = (
        Index("ix_batches_started_by", "started_by"),
        Index("ix_batches_status", "status"),
        Index("ix_processing_batches_company_id", "company_id"),
    )

    started_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )

    status: Mapped[BatchStatus] = mapped_column(
        Enum(
            BatchStatus,
            name="batch_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=BatchStatus.CREATED,
        server_default=BatchStatus.CREATED.value,
    )

    total_invoices: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    starter: Mapped["User"] = relationship(lazy="raise")
    invoices: Mapped[list["MatchHistory"]] = relationship(
        back_populates="batch", lazy="raise"
    )
