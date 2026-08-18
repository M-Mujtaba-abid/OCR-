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
    #: Tax printed against this line. Null on the many invoices that state tax
    #: once for the whole document rather than per line.
    tax_amount: float | None = None
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


class UploadTicketRequest(BaseModel):
    """One file the browser is about to send straight to storage."""

    file_name: str = Field(min_length=1, max_length=255)
    #: Declared by the client and signed into the URL, so the object cannot be
    #: stored as anything else. It is NOT trusted as the truth about the file —
    #: the bytes are sniffed after upload, in `register`.
    content_type: str = Field(min_length=3, max_length=100)


class UploadTicket(BaseModel):
    """Where to PUT one file, and what to call it afterwards."""

    #: Server-generated, always. A client-supplied key would let one tenant
    #: write into another's prefix.
    key: str
    upload_url: str
    #: Echoed back so the browser sends exactly the headers that were signed.
    content_type: str
    file_name: str


class UploadTicketsRequest(BaseModel):
    files: list[UploadTicketRequest] = Field(min_length=1)


class RegisterUploadRequest(BaseModel):
    """One object the browser says it finished uploading."""

    key: str = Field(min_length=1, max_length=512)
    file_name: str = Field(min_length=1, max_length=255)


class RegisterUploadsRequest(BaseModel):
    files: list[RegisterUploadRequest] = Field(min_length=1)
    member_ref_no: str | None = Field(default=None, max_length=120)
    member_notes: str | None = None


class InvoiceTrendPoint(BaseModel):
    """One day on the dashboard's trend chart."""

    day: dt.date
    #: Invoices that arrived that day.
    received: int
    #: Invoices a reviewer settled that day — confirmed, corrected, rejected or
    #: turned into a purchase order. Counted against `reviewed_at`, so it is
    #: about the day the WORK happened, not the day the document arrived.
    reviewed: int


class InvoiceTrend(BaseModel):
    """A continuous run of days, quiet ones included.

    Zero-filled deliberately: a chart that omits empty days draws a busy week
    and a dead one the same width, which is the opposite of what a trend is for.
    """

    days: int
    points: list[InvoiceTrendPoint] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Creating a purchase order from an invoice
# ---------------------------------------------------------------------------
class OdooMatchRead(BaseModel):
    """One Odoo record a piece of extracted text might refer to."""

    id: int
    name: str
    #: 0-100. Shown to the reviewer, because "Lemon 77 / Sanitized lemon 77" is
    #: the reason they are being asked rather than told.
    score: float


class PoPreviewLine(BaseModel):
    """One invoice line and the Odoo products it might mean."""

    line_no: int
    description: str
    quantity: float
    unit_price: float
    subtotal: float
    candidates: list[OdooMatchRead]
    #: Filled only where the answer is not really a choice. Null means the
    #: reviewer must pick — including where a wrong candidate scored well.
    preselected_product_id: int | None = None


class PoPreview(BaseModel):
    """What would be created, before anything is.

    Everything here is a proposal. The vendor is resolved or it is null, and a
    null blocks the whole thing; products are offered and never assumed.
    """

    vendor_name: str | None = None
    vendor: OdooMatchRead | None = None
    order_date: str | None = None
    currency: str = "USD"
    lines: list[PoPreviewLine] = Field(default_factory=list)
    #: The Odoo base URL, so the client can deep-link without its own copy of
    #: the setting.
    odoo_url: str = ""


class CreatePoLine(BaseModel):
    product_id: int = Field(gt=0, description="The Odoo product the reviewer chose.")
    description: str = Field(min_length=1, max_length=512)
    quantity: float = Field(ge=0)
    unit_price: float = Field(ge=0)


class CreatePoRequest(BaseModel):
    """The reviewer's approved mapping, not the extraction.

    The ids are sent explicitly rather than re-resolved server-side: resolving
    twice can produce two different answers, and the one that matters is the
    one a person looked at.
    """

    partner_id: int = Field(gt=0)
    order_date: str | None = None
    lines: list[CreatePoLine] = Field(min_length=1)
