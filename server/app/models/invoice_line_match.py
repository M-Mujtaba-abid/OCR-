"""One row per line item read off an invoice.

Written at extraction time from `InvoiceExtraction.items`, with the matching
columns left null. Product mapping — resolving `raw_description` to an Odoo
`product.product` — is a later phase; the columns exist now so that adding it
does not mean migrating a table that by then holds production data.

Kept as rows rather than left inside `match_history.extracted_json` because
line matching needs to be queried and corrected per line, and a reviewer
overriding line 4 of 12 must not rewrite a JSON blob to do it.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.match_history import MatchHistory


class LineMatchStatus(str, enum.Enum):
    PENDING = "pending"
    AUTO_MATCHED = "auto_matched"  # knowledge base or fuzzy hit
    LLM_MATCHED = "llm_matched"  # AI suggested
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"  # a human overrode the suggestion
    UNMATCHED = "unmatched"


class InvoiceLineMatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoice_line_matches"
    __table_args__ = (
        Index("ix_line_matches_history", "match_history_id"),
        Index("ix_line_matches_status", "status"),
    )

    # CASCADE: a line has no meaning without its invoice, unlike the invoice
    # itself which survives its uploader being deleted.
    match_history_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match_history.id", ondelete="CASCADE"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # ------------------------------------------------------- as printed
    raw_description: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_product_code: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[float | None] = mapped_column(Float)
    uom: Mapped[str | None] = mapped_column(String(50))
    unit_price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    tax_amount: Mapped[float | None] = mapped_column(Float)

    # ------------------------------------------------------- match result
    # Odoo ids, deliberately plain integers: that data lives in Odoo, so a
    # foreign key would be unenforceable.
    matched_product_id: Mapped[int | None] = mapped_column(Integer)
    matched_product_name: Mapped[str | None] = mapped_column(String(255))
    matched_po_line_id: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    provider_code: Mapped[str | None] = mapped_column(String(50))

    # ------------------------------------------------------- review
    status: Mapped[LineMatchStatus] = mapped_column(
        # values_callable, or SQLAlchemy persists the member NAME ("PENDING")
        # rather than its value ("pending") and the API contract breaks.
        Enum(
            LineMatchStatus,
            name="line_match_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=LineMatchStatus.PENDING,
        server_default=LineMatchStatus.PENDING.value,
    )
    final_product_id: Mapped[int | None] = mapped_column(Integer)

    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    invoice: Mapped["MatchHistory"] = relationship(
        back_populates="lines", lazy="raise"
    )
