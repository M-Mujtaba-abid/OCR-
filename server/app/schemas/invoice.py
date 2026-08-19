"""Invoice request/response schemas."""

from __future__ import annotations

import datetime as dt
import enum
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
    #: A property over `extra`, not a column — see `MatchHistory.odoo_bill_ref`.
    #: `from_attributes` reads properties, so nothing else is needed.
    odoo_bill_ref: str | None = None

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


# ---------------------------------------------------------------------------
# Creating a vendor bill from a matched purchase order
#
# One order is billed across several invoices over weeks — 100 pieces ordered,
# 50 delivered and billed now, 50 next month. Everything here is shaped by that:
# quantities are per line, "remaining" is read from Odoo rather than remembered,
# and an order that already carries a bill is the normal case rather than a
# duplicate.
# ---------------------------------------------------------------------------
class BillOutcome(str, enum.Enum):
    """What actually happened in Odoo.

    Three outcomes rather than a boolean, because "no bill was created" splits
    into two answers that need very different responses from a person: a bill
    that exists and is unpaid can still be corrected, a paid one cannot.
    """

    BILL_CREATED = "bill_created"
    BILL_EXISTS = "bill_exists"
    ALREADY_PAID = "already_paid"


class AttachmentStatus(str, enum.Enum):
    """Whether the source document made it onto the bill.

    Never a reason to fail the request. The bill is the accounting record and it
    exists by the time this is decided; a missing PDF is an inconvenience
    somebody fixes by hand, and unwinding a bill to avoid it would be the far
    larger problem.
    """

    ATTACHED = "attached"
    SKIPPED = "skipped"
    FAILED = "failed"


class BillPreviewLine(BaseModel):
    """One purchase-order line, and what this invoice proposes to bill on it.

    All four quantities are shown because they answer four different questions
    and a reviewer needs every one: `ordered` is what was agreed, `received` is
    what arrived, `billed` is what earlier bills already claimed, and
    `remaining` is the only figure they may actually spend. Showing `remaining`
    alone would hide whether a low number means a small order or one that is
    nearly fully billed.
    """

    po_line_id: int
    product_id: int | None = None
    product_name: str | None = None
    #: The order line's own wording — what Odoo will print on the bill.
    description: str
    uom: str | None = None

    ordered_qty: float
    #: Billing beyond what has arrived is legitimate (a prepayment, a service),
    #: so this informs the reviewer rather than constraining them.
    received_qty: float
    #: Odoo's `qty_invoiced` — the sum of every bill already raised against this
    #: line. This is what makes partial billing safe: it is Odoo's number, read
    #: fresh, not one this system remembers between invoices.
    billed_qty: float
    #: ordered - billed. The ceiling the create endpoint enforces, restated here
    #: so the screen can refuse before the request is even made.
    remaining_qty: float

    #: What the server proposes: the invoice's quantity, capped at `remaining`.
    #: Zero where nothing on the invoice mapped to this line — the reviewer may
    #: still type a quantity in, which is why the line is shown at all.
    proposed_qty: float
    #: The ORDER's price, not the invoice's. Odoo bills at the agreed price by
    #: design; a disagreement is surfaced below rather than silently applied.
    unit_price: float

    # ------------------------------------------------ where the proposal came from
    #: The invoice line this was mapped from, by position. Null when unmatched.
    invoice_line_no: int | None = None
    invoice_description: str | None = None
    invoice_quantity: float | None = None
    #: The invoice's own unit price, so a price disagreement is visible before
    #: the bill is posted rather than after somebody reconciles it.
    invoice_unit_price: float | None = None
    #: 0-100 similarity of the two descriptions. Null when unmatched. Shown for
    #: the same reason `OdooMatchRead.score` is: a 76 and a 99 are both
    #: "matched", and the reviewer is entitled to tell them apart.
    match_score: float | None = None

    #: Odoo's effective tax rate on this line, as a fraction — 0.05 is 5%. A
    #: rate rather than an amount because the reviewer edits the quantity, and
    #: an amount computed for the proposed one would be wrong the moment they
    #: do. Zero means the line carries no tax in Odoo, which is worth seeing per
    #: line: one untaxed line is what makes a whole bill disagree with the paper.
    tax_rate: float = 0.0


class BillPreviewUnmatchedLine(BaseModel):
    """An invoice line that mapped to nothing on the order.

    Listed rather than dropped. A line on the paper with no counterpart on the
    order is either an extra the vendor added or a description this system could
    not recognise, and both need a person — silently omitting it makes the bill
    quietly short with nothing on screen saying so.
    """

    line_no: int
    description: str
    quantity: float
    unit_price: float
    subtotal: float


class BillDuplicate(BaseModel):
    """A bill Odoo already holds for this reference and this vendor."""

    bill_id: int
    bill_ref: str
    #: Odoo's `state`: draft or posted.
    state: str | None = None
    #: Odoo's `payment_state`: not_paid, in_payment, paid, partial, reversed.
    payment_state: str | None = None
    amount_total: float = 0.0
    #: What the create endpoint would answer if it were called right now.
    outcome: BillOutcome


class BillPreview(BaseModel):
    """What billing this invoice against its order would produce.

    Read-only and safe to call repeatedly. Nothing here is a decision — the
    create endpoint takes the mapping back from the reviewer and does not
    re-derive it.
    """

    po_id: int
    po_name: str
    partner_id: int | None = None
    partner_name: str | None = None
    #: A draft RFQ cannot be billed, and saying so here is what stops the
    #: reviewer discovering it at creation time.
    po_state: str | None = None
    currency: str | None = None

    #: What will be written to the bill's `ref` — the vendor's own invoice
    #: number, which is also the key the duplicate guard searches on.
    invoice_ref: str | None = None
    #: The OCR'd date, or today. Resolved here so the screen shows the date that
    #: will actually be used rather than a blank the server fills in later.
    invoice_date: dt.date

    #: Non-null means Odoo already holds a bill for this reference. The screen
    #: shows it instead of a create button; the create endpoint refuses
    #: independently.
    duplicate: BillDuplicate | None = None
    #: This invoice row has already produced a bill. Local, not from Odoo.
    already_pushed: bool = False

    lines: list[BillPreviewLine] = Field(default_factory=list)
    unmatched: list[BillPreviewUnmatchedLine] = Field(default_factory=list)

    #: The proposal at the ORDER's prices, and the invoice's own figures, side
    #: by side. They routinely differ and a reviewer should see the gap before
    #: posting, not after.
    #:
    #: Tax is carried for the same reason the untaxed total is. Odoo computes it
    #: from the product and the fiscal position, so a bill can come out at the
    #: untaxed amount while the vendor's paper charges 5% VAT — and showing only
    #: the untaxed figure made that look like the bill was dropping the tax when
    #: it was the order that carried none.
    proposed_untaxed: float = 0.0
    proposed_tax: float = 0.0
    proposed_total: float = 0.0
    invoice_untaxed: float | None = None
    invoice_tax: float | None = None
    invoice_total: float | None = None

    #: The Odoo base URL, so the client deep-links without its own copy of the
    #: setting — exactly as `PoPreview.odoo_url` does.
    odoo_url: str = ""


class CreateBillLine(BaseModel):
    """One approved line. Ids and quantities only — never descriptions.

    Odoo derives the bill line's product, name, price and taxes from
    `purchase_line_id`. Sending our own would be this system overwriting an
    ERP's configuration with an OCR reading, which is the mistake
    `create_draft_purchase_order` already refuses to make with `taxes_id`.
    """

    po_line_id: int = Field(gt=0)
    quantity: float = Field(
        gt=0,
        description=(
            "Must be greater than zero. A zero-quantity line is one the "
            "reviewer meant to leave off, so the client omits it rather than "
            "sending a no-op."
        ),
    )


class CreateBillRequest(BaseModel):
    """The reviewer's approved mapping, not the extraction.

    Same rule as `CreatePoRequest`: the server does not re-resolve. A preview
    minutes old and a fresh resolution can disagree, and the one that matters is
    the one a person looked at. What the server DOES re-check is Odoo's side —
    that the order still exists, that the lines still belong to it, and that
    nothing has been billed against them in the meantime.
    """

    po_id: int = Field(
        gt=0,
        description=(
            "The order to bill against. Must be the invoice's confirmed match — "
            "changing which order an invoice belongs to is what /confirm is "
            "for, and doing it here would bypass the `was_corrected` record."
        ),
    )
    #: Defaults to the OCR'd invoice number server-side. Overridable because a
    #: mangled reference is exactly what defeats the duplicate guard, and the
    #: reviewer can read the paper.
    ref: str | None = Field(default=None, max_length=120)
    #: Defaults to the OCR'd date, then today.
    invoice_date: dt.date | None = None
    lines: list[CreateBillLine] = Field(min_length=1)
    #: Record the goods receipt in Odoo for these quantities before billing.
    #: On by default: under Odoo's own bill-control policy an unreceipted order
    #: has nothing to invoice, so skipping this usually means no bill at all.
    receive_goods: bool = True
    #: Pull the source document out of storage and attach it to the bill. On by
    #: default: a bill without its evidence is what an audit fails on.
    attach_document: bool = True


class CreateBillResult(BaseModel):
    """The bill, and the invoice as it now stands.

    Both, deliberately. The bill fields are what the screen reports; `invoice`
    is what the client writes straight into its detail cache, the way every
    other mutation here does. Returning only the first would leave the cached
    invoice claiming it had never been pushed, and the panel would offer to
    create the bill a second time.
    """

    status: BillOutcome
    #: Populated for all three outcomes — "a bill already exists" is useless
    #: without saying which one.
    bill_id: int | None = None
    #: For a draft bill this is the vendor's own invoice number, not an Odoo
    #: sequence: Odoo assigns BILL/2026/08/0001 at post time and these bills are
    #: deliberately left in draft, where `name` is literally "/".
    bill_ref: str | None = None
    attachment_status: AttachmentStatus = AttachmentStatus.SKIPPED
    #: The bill's accounting date, as Odoo now holds it.
    invoice_date: dt.date
    #: Ready to open. Built server-side because the URL shape belongs next to
    #: the code that knows the Odoo version.
    bill_url: str = ""
    #: The goods receipt this created, when it created one.
    receipt_name: str | None = None
    backorder_names: list[str] = Field(default_factory=list)
    invoice: InvoiceDetail


# ---------------------------------------------------------------------------
# Bill history
#
# What was actually billed, after the fact. Every field below is read back out
# of `MatchHistory.extra["odoo_bill"]` — the record written at creation time —
# rather than re-fetched from Odoo. That is the point of a history: it reports
# what this system did, at the moment it did it, and stays answerable when
# Odoo is unreachable or somebody has since edited the bill there.
# ---------------------------------------------------------------------------
class BillHistoryItem(BaseModel):
    """One vendor bill this system raised in Odoo, with its invoice and order."""

    #: The invoice it came from — the id the review screen is routed by.
    invoice_id: uuid.UUID
    file_name: str
    member_ref_no: str | None = None

    #: What the document said. Kept beside the Odoo figures deliberately: a
    #: bill that does not agree with its invoice is the thing worth finding in
    #: a history, and it cannot be seen if only one of the two is shown.
    vendor: str | None = None
    invoice_no: str | None = None
    invoice_total: float | None = None
    currency: str | None = None

    #: The bill. `bill_ref` is the vendor's own number, not an Odoo sequence —
    #: these are left in draft and Odoo does not number a bill until it posts.
    bill_id: int | None = None
    bill_ref: str | None = None
    bill_amount: float | None = None
    bill_date: dt.date | None = None
    #: Empty when Odoo has no base URL configured, which is how the client
    #: knows not to render a dead link.
    bill_url: str = ""
    attachment_status: str | None = None

    #: The purchase order it was billed against.
    po_id: int | None = None
    po_name: str | None = None
    po_url: str = ""

    #: The goods receipt, when one was recorded, and what is still to come.
    receipt_name: str | None = None
    backorder_names: list[str] = Field(default_factory=list)
    #: Order lines the bill actually carried, per the mapping that was used.
    line_count: int = 0

    #: True when the reviewer billed an order the matcher had not suggested.
    was_corrected: bool = False
    billed_at: dt.datetime | None = None
    uploader: UploaderRead | None = None
    #: Who approved it. Null once that account is deleted — the FK is SET NULL,
    #: because losing a user must not erase the billing record.
    reviewer: UploaderRead | None = None
