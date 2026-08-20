"""Match history — one row per uploaded invoice.

This single table is the audit log, the work queue, and (later) the training
data for the matching engine. Splitting it would mean joining three tables to
answer "what happened to this invoice", which is the question asked most often.

Only the upload-time columns are populated today. Everything from `ocr_provider`
onward is written by later pipeline stages and is nullable for that reason — the
columns exist now so that adding OCR does not require a migration on a table
that by then holds production data.

Pipeline:
    uploaded -> ocr_queued -> ocr_processing -> ocr_done
             -> matching -> pending_review | no_match
             -> confirmed | corrected | rejected -> pushed
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.company import CompanyScopedMixin

if TYPE_CHECKING:
    from app.models.invoice_line_match import InvoiceLineMatch
    from app.models.processing_batch import ProcessingBatch
    from app.models.user import User


class InvoiceStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    OCR_QUEUED = "ocr_queued"
    OCR_PROCESSING = "ocr_processing"
    OCR_FAILED = "ocr_failed"
    OCR_DONE = "ocr_done"
    MATCHING = "matching"
    MATCH_FAILED = "match_failed"
    PENDING_REVIEW = "pending_review"
    NO_MATCH = "no_match"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    # A draft purchase order was created in Odoo from this invoice, because no
    # existing order matched it. Terminal here, but not in Odoo: what was
    # created is an RFQ somebody still has to confirm there.
    PO_CREATED = "po_created"
    # Somebody asked for the bill and an approval chain is running. The status
    # the invoice held on the way in is kept on the request and restored when
    # the chain finishes, whichever way it goes — so this is a detour, not a
    # step, and nothing downstream should treat it as a new resting place.
    PENDING_APPROVAL = "pending_approval"
    PUSHED = "pushed"


#: Statuses an admin may still act on. Kept next to the enum so the queue
#: definition does not drift from the state machine it filters.
OPEN_STATUSES: frozenset[InvoiceStatus] = frozenset(
    {
        InvoiceStatus.UPLOADED,
        InvoiceStatus.OCR_FAILED,
        InvoiceStatus.MATCH_FAILED,
        InvoiceStatus.PENDING_REVIEW,
        InvoiceStatus.NO_MATCH,
        # Waiting on a person is still open work. Leaving it out would let an
        # invoice sit in an approval chain nobody is chasing while the dashboard
        # counts it as finished.
        InvoiceStatus.PENDING_APPROVAL,
    }
)

#: Statuses from which a member may still withdraw their own upload.
#:
#: Deliberately wider than "just uploaded". Extraction now starts within
#: milliseconds of the upload, so gating on UPLOADED alone would leave a
#: withdraw window of about two seconds — a feature that exists but can never
#: be used.
#:
#: What genuinely ends the window is a human taking it on: once an invoice
#: reaches PENDING_REVIEW an admin is looking at it, and past that it has been
#: decided. Machine processing is reversible and does not count; the two
#: in-flight states are excluded only because deleting a row mid-write is
#: asking for a torn update.
WITHDRAWABLE_STATUSES: frozenset[InvoiceStatus] = frozenset(
    {
        InvoiceStatus.UPLOADED,
        InvoiceStatus.OCR_QUEUED,
        InvoiceStatus.OCR_FAILED,
        InvoiceStatus.OCR_DONE,
        InvoiceStatus.MATCH_FAILED,
        InvoiceStatus.NO_MATCH,
    }
)


class MatchHistory(UUIDPrimaryKeyMixin, CompanyScopedMixin, TimestampMixin, Base):
    __tablename__ = "match_history"
    __table_args__ = (
        Index("ix_match_history_status", "status"),
        Index("ix_match_history_batch", "batch_id"),
        Index("ix_match_history_uploader", "uploaded_by"),
        # The admin queue's exact query shape: "open invoices for this company,
        # newest first". A composite beats two single-column indexes here
        # because Postgres can then satisfy the filter and the sort together.
        Index("ix_match_history_company_status", "company_id", "status"),
        Index("ix_match_history_created", "created_at"),
    )

    # ------------------------------------------------------------- ownership
    # SET NULL rather than CASCADE: deleting a user must not erase the invoice
    # history that accounting relies on.
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="SET NULL")
    )

    # ------------------------------------------------- member-supplied context
    member_ref_no: Mapped[str | None] = mapped_column(String(120))
    member_notes: Mapped[str | None] = mapped_column(Text)

    # ------------------------------------------------------------- source file
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The object key inside the bucket. This — not file_url — is the durable
    # identifier: the bucket is private, so every read is a freshly signed URL
    # generated from this key. Not in the original DBML; added because a stored
    # URL for a private object is either unusable or expired.
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    page_count: Mapped[int | None] = mapped_column(Integer)

    # --------------------------------------------------------------- OCR stage
    ocr_provider: Mapped[str | None] = mapped_column(String(50))
    ocr_model: Mapped[str | None] = mapped_column(String(100))
    ocr_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    detected_language: Mapped[str | None] = mapped_column(String(10))
    ocr_completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ocr_error: Mapped[str | None] = mapped_column(Text)

    # --------------------------------------------------------- extracted fields
    # The validated InvoiceExtraction, whole. The promoted scalars below are
    # duplicated out of it purely so they can be indexed and filtered on —
    # this column is the record of what was actually read.
    extracted_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    extracted_vendor: Mapped[str | None] = mapped_column(String(255))
    extracted_invoice_no: Mapped[str | None] = mapped_column(String(120))
    extracted_date: Mapped[dt.date | None] = mapped_column(Date)
    extracted_total: Mapped[float | None] = mapped_column(Float)
    extracted_currency: Mapped[str | None] = mapped_column(String(10))
    extracted_tax: Mapped[float | None] = mapped_column(Float)
    # The comparator for matching: a purchase order's amount_untaxed is the
    # figure that reliably agrees with an invoice, because tax treatment
    # differs between the two documents far more often than the goods do.
    extracted_untaxed: Mapped[float | None] = mapped_column(Float)
    extracted_line_count: Mapped[int | None] = mapped_column(Integer)

    # ----------------------------------------------------------- match results
    # Plain integers, deliberately not foreign keys: these identify records in
    # Odoo, which is a different database. A FK would be unenforceable.
    matched_po_id: Mapped[int | None] = mapped_column(Integer)
    matched_po_name: Mapped[str | None] = mapped_column(String(120))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    candidates: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    match_strategy: Mapped[str | None] = mapped_column(String(30))
    # The model's stated reasoning, promoted out of `candidates` so the review
    # screen can show it without unpacking a blob. This is what makes a wrong
    # match arguable rather than mysterious.
    match_reasoning: Mapped[str | None] = mapped_column(Text)

    # ----------------------------------------------------------------- status
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(
            InvoiceStatus,
            name="invoice_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=InvoiceStatus.UPLOADED,
        server_default=InvoiceStatus.UPLOADED.value,
    )
    was_corrected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    final_po_id: Mapped[int | None] = mapped_column(Integer)
    pushed_to_odoo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    pushed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    odoo_bill_id: Mapped[int | None] = mapped_column(Integer)

    # ------------------------------------------------------------------ audit
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Two FKs point at users, so the join condition must be stated explicitly —
    # SQLAlchemy cannot guess which one each relationship means.
    uploader: Mapped["User | None"] = relationship(
        foreign_keys=[uploaded_by], lazy="raise"
    )
    reviewer: Mapped["User | None"] = relationship(
        foreign_keys=[reviewed_by], lazy="raise"
    )
    batch: Mapped["ProcessingBatch | None"] = relationship(
        back_populates="invoices", lazy="raise"
    )
    lines: Mapped[list["InvoiceLineMatch"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineMatch.line_no",
        lazy="raise",
    )

    @property
    def odoo_bill_ref(self) -> str | None:
        """The label for the vendor bill this invoice became, out of `extra`.

        `odoo_bill_id` is the durable identifier and has its own column; this is
        the string a person actually reads. A property rather than a column
        because it is derived display data — the audit record in
        `extra["odoo_bill"]` is the source, and copying it into a column would
        be two places to keep in step.

        Note this is the vendor's own invoice number, not an Odoo sequence: the
        bills this system creates are left in draft, and Odoo does not number a
        bill until it is posted.
        """
        bill = (self.extra or {}).get("odoo_bill")
        if not isinstance(bill, dict):
            return None
        label = bill.get("display_name") or bill.get("vendor_ref")
        return str(label) if label else None
