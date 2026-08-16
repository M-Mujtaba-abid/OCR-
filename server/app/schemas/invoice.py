"""Invoice request/response schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.match_history import InvoiceStatus


class UploaderRead(BaseModel):
    """Just enough about the uploader for an admin list row.

    Not the full UserRead: an invoice list is not a user directory, and
    embedding the whole user leaks `is_verified`/`updated_at` into a context
    that has no use for them.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None


class InvoiceListItem(BaseModel):
    """One row in a list. Deliberately excludes ocr_text / ocr_raw / candidates
    — those are hundreds of KB each and would make a 20-row page enormous."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_name: str
    file_size_bytes: int | None = None
    mime_type: str | None = None
    page_count: int | None = None
    member_ref_no: str | None = None
    status: InvoiceStatus
    extracted_vendor: str | None = None
    extracted_invoice_no: str | None = None
    extracted_total: float | None = None
    extracted_currency: str | None = None
    matched_po_name: str | None = None
    confidence_score: float | None = None
    created_at: dt.datetime
    updated_at: dt.datetime

    uploader: UploaderRead | None = None


class InvoiceLineRead(BaseModel):
    """One extracted line item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_no: int
    raw_description: str
    #: The SKU printed on the line, when the vendor quotes one. This is what
    #: turns line matching from fuzzy description comparison into an exact
    #: lookup, so it is worth surfacing on the review screen.
    raw_product_code: str | None = None
    uom: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    matched_product_id: int | None = None
    matched_product_name: str | None = None
    confidence: float | None = None
    status: str


class InvoiceDetail(InvoiceListItem):
    """Everything except the raw OCR blob, which is fetched separately when
    somebody actually wants to debug an extraction."""

    tenant_id: str
    member_notes: str | None = None
    batch_id: uuid.UUID | None = None

    # The validated extraction, verbatim. The review screen renders this next
    # to the PDF so a human can see exactly what the model read.
    extracted_json: dict[str, Any] | None = None
    extracted_untaxed: float | None = None
    #: Ranked candidates with their score breakdown. Null until matching runs.
    candidates: dict[str, Any] | None = None
    match_reasoning: str | None = None

    lines: list[InvoiceLineRead] = Field(default_factory=list)

    ocr_provider: str | None = None
    ocr_model: str | None = None
    ocr_confidence: float | None = None
    detected_language: str | None = None
    ocr_completed_at: dt.datetime | None = None
    ocr_error: str | None = None

    extracted_date: dt.date | None = None
    extracted_tax: float | None = None
    extracted_line_count: int | None = None

    matched_po_id: int | None = None
    match_strategy: str | None = None
    was_corrected: bool
    final_po_id: int | None = None
    pushed_to_odoo: bool
    pushed_at: dt.datetime | None = None
    odoo_bill_id: int | None = None

    reviewed_at: dt.datetime | None = None
    rejection_reason: str | None = None


class UploadRejection(BaseModel):
    """One file that did not make it.

    A partial success has to be reportable: rejecting all ten files because the
    seventh was a Word document would be hostile, and silently dropping it
    would be worse.
    """

    file_name: str
    reason: str
    code: str


class UploadResult(BaseModel):
    uploaded: list[InvoiceListItem]
    rejected: list[UploadRejection] = Field(default_factory=list)


class InvoiceStats(BaseModel):
    total: int
    #: Every status, zero-filled, so the dashboard renders a stable set of
    #: cards instead of ones that appear and vanish.
    by_status: dict[InvoiceStatus, int]
    #: Statuses an admin can still act on — the "inbox" number.
    open_count: int


class FileLink(BaseModel):
    """A short-lived signed URL for one stored object."""

    url: str
    expires_in: int = Field(description="Seconds until the signature expires.")
    file_name: str
    mime_type: str | None = None


class JobAccepted(BaseModel):
    """The body of a 202. The work is scheduled, not done.

    Carries the status the row moved to so the client knows what to poll for
    rather than guessing.
    """

    id: uuid.UUID
    status: InvoiceStatus
    message: str


class ConfirmMatchRequest(BaseModel):
    po_id: int = Field(
        gt=0,
        description=(
            "The Odoo purchase order to attach. May differ from the suggested "
            "one — that is an override, and it is recorded as such."
        ),
    )


class RejectInvoiceRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
