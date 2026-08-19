"""Create a vendor bill in Odoo from an invoice and the order it matched.

The last step in the pipeline, and the only one that moves money.
`po_creator_service` writes a draft nobody owes anything on; this writes an
accounts-payable document.

Three things make that safe to do from a review screen:

  * **The mapping is approved, never resolved.** The preview proposes which
    order line each invoice line means; the create endpoint takes that proposal
    back from the reviewer verbatim. Resolving twice can produce two answers,
    and only one of them was looked at by a person.

  * **Odoo owns the quantities.** `qty_invoiced` on each order line is the sum
    of every bill already raised against it, so "how much is left" is read from
    Odoo at create time rather than tracked here. That is what makes partial
    billing correct across bills raised weeks apart: this system never has to
    remember what it did last time.

  * **Duplicates are refused before the write, not detected after it.** The
    guard is the vendor's own invoice number against the same vendor. A vendor
    re-sending a bill is routine; paying it twice is not.

What this deliberately does NOT send: products, prices, descriptions or taxes.
Every bill line is `{purchase_line_id, quantity}` and Odoo derives the rest from
the order. An OCR'd price overwriting an ERP's agreed price is not a smaller
error than no bill at all.

One order can carry several bills. 100 pieces ordered, 50 delivered and billed
now, 50 next month — so an order that already has a bill is the normal case and
must not be refused as a duplicate.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InvoiceNotReadyError, OverBilledError
from app.lib.logging import get_logger
from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.notification import NotificationType
from app.repositories.match_history_repository import MatchHistoryRepository
from app.schemas.extraction import ExtractedLineItem, InvoiceExtraction
from app.schemas.invoice import AttachmentStatus, BillOutcome
from app.schemas.odoo import (
    PAID_PAYMENT_STATES,
    OdooAttachment,
    OdooExistingBill,
    OdooPurchaseOrderLine,
)
from app.services.matching_engine import normalise_vendor
from app.services.notification_service import NotificationService
from app.services.odoo_service import BILLABLE_PO_STATES, odoo_service
from app.services.source_document import read_source_document

logger = get_logger(__name__)

#: The same threshold `matching_engine._score_lines` uses, and shared on
#: purpose: the reviewer already saw a line-items score computed at 75, and a
#: preview pairing lines by a different rule would disagree with the number that
#: got them to this screen.
LINE_MATCH_FLOOR = 75.0

#: Quantities below this are rounding, not a line. Odoo stores quantities as
#: floats, so `ordered - billed` on a fully billed line lands on 1e-14 rather
#: than 0 and would otherwise render as a "remaining" that is not zero.
QTY_EPSILON = 1e-6

#: Where a bill lives in this Odoo's web UI. One definition, server-side,
#: because the URL shape is version-specific and a client that builds its own
#: has a second copy to forget.
BILL_URL_TEMPLATE = "{base}/odoo/action-account.action_move_in_invoice_type/{bill_id}"

#: And where its purchase order lives. Same reasoning, same place.
PO_URL_TEMPLATE = "{base}/odoo/purchase/{po_id}"


@dataclass(frozen=True)
class ProposedPair:
    """One invoice line paired with one order line, and how sure that is."""

    invoice_line_no: int
    item: ExtractedLineItem
    score: float


# ---------------------------------------------------------------------------
# Pure decision logic
#
# No I/O and no clock. This is the judgement the feature rests on, and it is
# testable against literals precisely because none of it reaches out.
# ---------------------------------------------------------------------------
def remaining_to_bill(line: OdooPurchaseOrderLine) -> float:
    """How much of this order line has not been billed yet.

    Ordered minus invoiced, floored at zero. NOT received minus invoiced:
    billing ahead of delivery is legitimate — a prepayment, a service, a
    part-shipment invoiced in full — and refusing it would block correct bills
    to prevent a case Odoo itself permits. `received` is shown to the reviewer
    instead, which is where that judgement belongs.
    """
    if line.display_type:
        return 0.0
    return max(0.0, line.product_qty - line.qty_invoiced)


def tax_rate_of(line: OdooPurchaseOrderLine) -> float:
    """The effective tax rate Odoo applies to this order line, as a fraction.

    Read off the ORDER's own figures — `price_tax` over `price_subtotal` — and
    never off the invoice. Odoo owns tax: the rate lives on the product and the
    fiscal position, an OCR'd figure must not overwrite it, and a reviewer who
    disagrees fixes it in Odoo rather than here. This only reports what the
    bill is going to say.

    A ratio rather than the tax ids, because a line can carry several taxes at
    once and reimplementing Odoo's compounding rules to add them up is the
    wrong thing to own. Scaling Odoo's own answer is exact for percentage
    taxes, which is what these lines carry, and proportionate for the rest.

    A rate rather than an amount because the reviewer edits the quantity on
    screen, and a figure computed for the proposed quantity would be wrong the
    moment they do.

    Zero when the line carries no tax. That is a real answer, not a missing
    one: it is exactly how a bill comes out short against an invoice charging
    5% VAT, and the screen now shows both figures so it is seen beforehand.
    """
    if not line.price_subtotal or not line.price_tax:
        return 0.0
    return line.price_tax / line.price_subtotal


def propose_mapping(
    items: list[ExtractedLineItem], po_lines: list[OdooPurchaseOrderLine]
) -> tuple[dict[int, ProposedPair], list[int]]:
    """Pair invoice lines to order lines, greedily and one-to-one.

    The same algorithm as `matching_engine._score_lines`, and permissive for the
    same reason: a catalogue and a vendor's invoice genuinely word the same
    goods differently, and demanding near-identity would leave every line
    unmatched and every preview empty.

    One-to-one is the part that matters. Two invoice lines reading "Lemon"
    against a single order line for "Lemon" must not both claim it — that is how
    a quantity gets billed twice inside one bill, which no per-line remaining
    check would catch.

    Returns the pairs keyed by order-line id, and the positions of invoice lines
    that found nothing.
    """
    billable = [line for line in po_lines if not line.display_type]
    order_names = [normalise_vendor(line.product_name or line.name) for line in billable]
    available = set(range(len(billable)))

    pairs: dict[int, ProposedPair] = {}
    unmatched: list[int] = []

    for line_no, item in enumerate(items, start=1):
        needle = normalise_vendor(item.name)
        if not needle:
            unmatched.append(line_no)
            continue

        best_index: int | None = None
        best_score = 0.0
        for index in available:
            if not order_names[index]:
                continue
            score = float(fuzz.token_set_ratio(needle, order_names[index]))
            if score > best_score:
                best_index, best_score = index, score

        if best_index is None or best_score < LINE_MATCH_FLOOR:
            unmatched.append(line_no)
            continue

        available.discard(best_index)
        pairs[billable[best_index].id] = ProposedPair(
            invoice_line_no=line_no, item=item, score=best_score
        )

    return pairs, unmatched


def classify_duplicate(
    bills: list[OdooExistingBill],
) -> tuple[OdooExistingBill, BillOutcome] | None:
    """The bill Odoo already holds for this reference, and what it means.

    Pure, so the rule can be tested without Odoo — the same shape as
    `odoo_service.match_recent_draft`.

    A paid bill outranks an unpaid one when several match: the worst case is the
    one the reviewer must be told about.
    """
    live = [bill for bill in bills if bill.state != "cancel"]
    if not live:
        return None

    paid = next((b for b in live if b.payment_state in PAID_PAYMENT_STATES), None)
    if paid is not None:
        return paid, BillOutcome.ALREADY_PAID
    return live[0], BillOutcome.BILL_EXISTS


def check_over_billing(
    approved: list[dict[str, Any]], po_lines: dict[int, OdooPurchaseOrderLine]
) -> str | None:
    """The message explaining what would be over-billed, or None.

    Message-producing rather than raising, so the wording is testable and the
    caller decides the status code. Every offending line is named with its
    remaining quantity: "over-billed" without saying by how much on which line
    leaves the reviewer to work it out against Odoo, which is the thing this
    product exists to avoid.

    Quantities are summed per line first. Two entries for one order line, each
    within the remaining quantity, can together exceed it — and checking them
    one at a time would let that through.
    """
    wanted: dict[int, float] = {}
    for line in approved:
        po_line_id = int(line["po_line_id"])
        wanted[po_line_id] = wanted.get(po_line_id, 0.0) + float(line["quantity"])

    problems: list[str] = []
    for po_line_id, quantity in sorted(wanted.items()):
        po_line = po_lines.get(po_line_id)
        if po_line is None:
            continue  # caught earlier, by the ownership check
        remaining = remaining_to_bill(po_line)
        if quantity - remaining > QTY_EPSILON:
            label = po_line.product_name or po_line.name or f"line {po_line_id}"
            problems.append(f"{label}: {quantity:g} asked for, {remaining:g} left to bill")

    if not problems:
        return None
    return (
        "This would bill more than the order has left. "
        + "; ".join(problems)
        + ". Reduce the quantities, or raise a separate order for the excess."
    )


def resolve_invoice_date(extracted: dt.date | None, today: dt.date) -> dt.date:
    """The bill's accounting date: what the document says, or today.

    `today` is a parameter rather than a clock read so this is testable, and so
    the preview and the create agree on it within one request.
    """
    return extracted or today


def bill_url(bill_id: int | None) -> str:
    """The Odoo deep link for a bill."""
    base = settings.odoo_base_url
    if not base or not bill_id:
        return ""
    return BILL_URL_TEMPLATE.format(base=base, bill_id=bill_id)


def po_url(po_id: int | None) -> str:
    """The Odoo deep link for a purchase order."""
    base = settings.odoo_base_url
    if not base or not po_id:
        return ""
    return PO_URL_TEMPLATE.format(base=base, po_id=po_id)


def bill_history_item(invoice: MatchHistory) -> dict[str, Any]:
    """One history row, read back out of the audit blob this module wrote.

    Deliberately no Odoo call. A history of a hundred bills would be a hundred
    round trips to render, it would show what Odoo holds *now* rather than what
    was created, and it would go blank the day Odoo is down — which is the day
    somebody most wants to look up what was already sent.

    Everything falls back to the promoted columns, because `extra["odoo_bill"]`
    is written by this module and any bill raised before it existed has only
    `odoo_bill_id` and `final_po_id` to go on. A history that hides those rows
    would under-report the very thing it is counting.
    """
    bill = (invoice.extra or {}).get("odoo_bill")
    if not isinstance(bill, dict):
        bill = {}

    bill_id = invoice.odoo_bill_id or bill.get("id")
    order_id = invoice.final_po_id or invoice.matched_po_id or bill.get("po_id")

    #: Only ever a date in the blob, but it is JSON and this is the one field a
    #: person reads as a date rather than as an opaque label.
    raw_date = bill.get("invoice_date")
    try:
        billed_date = dt.date.fromisoformat(raw_date) if raw_date else None
    except (TypeError, ValueError):
        billed_date = None

    backorders = bill.get("backorders")
    lines = bill.get("lines")

    return {
        "invoice_id": invoice.id,
        "file_name": invoice.file_name,
        "member_ref_no": invoice.member_ref_no,
        "vendor": invoice.extracted_vendor,
        "invoice_no": invoice.extracted_invoice_no,
        "invoice_total": invoice.extracted_total,
        "currency": invoice.extracted_currency,
        "bill_id": bill_id,
        # `odoo_bill_ref` is the model's own reader for this blob. Used rather
        # than re-derived so the label here and the one on the review screen
        # cannot drift apart.
        "bill_ref": invoice.odoo_bill_ref or bill.get("vendor_ref"),
        "bill_amount": bill.get("amount_total"),
        "bill_date": billed_date,
        "bill_url": bill_url(bill_id),
        "attachment_status": bill.get("attachment"),
        "po_id": order_id,
        "po_name": bill.get("po_name") or invoice.matched_po_name,
        "po_url": po_url(order_id),
        "receipt_name": bill.get("receipt"),
        "backorder_names": list(backorders) if isinstance(backorders, list) else [],
        "line_count": len(lines) if isinstance(lines, list) else 0,
        "was_corrected": bool(invoice.was_corrected),
        "billed_at": invoice.pushed_at,
        "uploader": invoice.uploader,
        "reviewer": invoice.reviewer,
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
async def build_bill_preview(invoice: MatchHistory) -> dict[str, Any]:
    """Everything the reviewer needs to approve a bill, and nothing more.

    A plain dict validated by the API schema at the boundary, the way
    `po_creator_service.build_preview` hands data upwards.
    """
    order = await _billable_order(invoice)
    extraction = InvoiceExtraction.model_validate(invoice.extracted_json)
    pairs, unmatched_nos = propose_mapping(extraction.items, order.lines)

    ref = (invoice.extracted_invoice_no or "").strip() or None
    invoice_date = resolve_invoice_date(invoice.extracted_date, dt.date.today())

    duplicate: dict[str, Any] | None = None
    if ref and order.partner_id:
        found = classify_duplicate(
            await odoo_service.find_vendor_bills(partner_id=order.partner_id, ref=ref)
        )
        if found is not None:
            bill, outcome = found
            duplicate = {
                "bill_id": bill.id,
                "bill_ref": bill.ref or bill.name,
                "state": bill.state,
                "payment_state": bill.payment_state,
                "amount_total": bill.amount_total,
                "outcome": outcome,
            }

    lines: list[dict[str, Any]] = []
    proposed_untaxed = 0.0
    proposed_tax = 0.0
    for po_line in order.lines:
        if po_line.display_type:
            continue
        remaining = remaining_to_bill(po_line)
        pair = pairs.get(po_line.id)
        # Never propose more than is left, even when the invoice says more. The
        # reviewer sees the invoice's own figure beside it and can argue with
        # the order in Odoo; proposing an impossible number would only be
        # refused by the create endpoint a moment later.
        proposed = min(pair.item.quantity, remaining) if pair else 0.0
        rate = tax_rate_of(po_line)
        line_untaxed = proposed * po_line.price_unit
        proposed_untaxed += line_untaxed
        proposed_tax += line_untaxed * rate

        lines.append(
            {
                "po_line_id": po_line.id,
                "product_id": po_line.product_id,
                "product_name": po_line.product_name,
                "description": po_line.name or po_line.product_name or "",
                "uom": po_line.uom,
                "ordered_qty": po_line.product_qty,
                "received_qty": po_line.qty_received,
                "billed_qty": po_line.qty_invoiced,
                "remaining_qty": remaining,
                "proposed_qty": proposed,
                "unit_price": po_line.price_unit,
                "tax_rate": round(rate, 6),
                "invoice_line_no": pair.invoice_line_no if pair else None,
                "invoice_description": pair.item.name if pair else None,
                "invoice_quantity": pair.item.quantity if pair else None,
                "invoice_unit_price": pair.item.unit_price if pair else None,
                "match_score": round(pair.score, 1) if pair else None,
            }
        )

    by_no = dict(enumerate(extraction.items, start=1))
    return {
        "po_id": order.id,
        "po_name": order.name,
        "partner_id": order.partner_id,
        "partner_name": order.partner_name,
        "po_state": order.state,
        "currency": order.currency,
        "invoice_ref": ref,
        "invoice_date": invoice_date,
        "duplicate": duplicate,
        "already_pushed": invoice.pushed_to_odoo,
        "lines": lines,
        "unmatched": [
            {
                "line_no": no,
                "description": by_no[no].name,
                "quantity": by_no[no].quantity,
                "unit_price": by_no[no].unit_price,
                "subtotal": by_no[no].subtotal,
            }
            for no in unmatched_nos
        ],
        "proposed_untaxed": round(proposed_untaxed, 2),
        "proposed_tax": round(proposed_tax, 2),
        "proposed_total": round(proposed_untaxed + proposed_tax, 2),
        "invoice_untaxed": extraction.untaxed_amount or None,
        "invoice_tax": extraction.tax_amount or None,
        "invoice_total": extraction.total_amount or None,
        "odoo_url": settings.odoo_base_url,
    }


async def _billable_order(invoice: MatchHistory):
    """The confirmed order, re-read from Odoo and checked it can carry a bill."""
    if not invoice.extracted_json:
        raise InvoiceNotReadyError("This invoice has not been read yet.")

    po_id = invoice.final_po_id or invoice.matched_po_id
    if po_id is None:
        raise InvoiceNotReadyError(
            "This invoice has no confirmed purchase order. Confirm the match "
            "first — which order a bill is raised against is a review decision, "
            "not something chosen at billing time.",
            code="NO_CONFIRMED_PO",
        )

    order = await odoo_service.fetch_purchase_order(po_id)
    if order is None:
        raise InvoiceNotReadyError(
            f"Purchase order {po_id} was not found in Odoo.", code="PO_NOT_FOUND"
        )
    if order.state not in BILLABLE_PO_STATES:
        raise InvoiceNotReadyError(
            f"{order.name} is {order.state or 'not confirmed'} in Odoo. Only a "
            f"confirmed order can be billed — confirm the RFQ there first.",
            code="PO_NOT_CONFIRMED",
        )
    return order


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
async def create_bill_for_invoice(
    db: AsyncSession,
    *,
    invoice: MatchHistory,
    po_id: int,
    ref: str | None,
    invoice_date: dt.date | None,
    lines: list[dict[str, Any]],
    receive_goods: bool,
    attach_document: bool,
    reviewer_id: uuid.UUID,
) -> tuple[MatchHistory, dict[str, Any]]:
    """Record the receipt, create the draft bill, and note it on the invoice.

    Everything is re-read from Odoo first, for the same reason
    `create_po_for_invoice` re-reads products: the preview the reviewer approved
    may be minutes old, and in that time another bill may have consumed the
    quantity this one is about to claim. Discovering that as a raw Odoo fault,
    after a partial write, is the failure worth an extra round trip to avoid.

    The checks run cheapest and most local first, and every one of them happens
    before `receive_purchase_order_lines` — which is the single call in this
    feature that cannot be undone.
    """
    if not invoice.extracted_json:
        raise InvoiceNotReadyError("This invoice has not been read yet.")

    # Local, free, and the check that catches an impatient second click before
    # any network call at all.
    if invoice.pushed_to_odoo or invoice.odoo_bill_id:
        raise InvoiceNotReadyError(
            f"A vendor bill was already created for this invoice "
            f"({invoice.odoo_bill_ref or invoice.odoo_bill_id}). Creating a "
            f"second would pay the vendor twice.",
            code="BILL_ALREADY_CREATED",
        )
    if not lines:
        raise InvoiceNotReadyError("A vendor bill needs at least one line.")

    # Which order an invoice belongs to is a review decision. Changing it here
    # would bypass `/confirm` and the `was_corrected` record it keeps.
    confirmed = invoice.final_po_id or invoice.matched_po_id
    if confirmed is not None and po_id != confirmed:
        raise InvoiceNotReadyError(
            f"This invoice is matched to purchase order {confirmed}, not "
            f"{po_id}. Change the match on the review screen first.",
            code="PO_MISMATCH",
        )

    order = await _billable_order(invoice)
    if not order.partner_id:
        raise InvoiceNotReadyError(
            f"{order.name} has no vendor in Odoo.", code="PARTNER_NOT_FOUND"
        )

    # Ownership. A stale or crafted po_line_id must never reach Odoo.
    by_id = {line.id: line for line in order.lines}
    strays = [
        str(line["po_line_id"]) for line in lines if int(line["po_line_id"]) not in by_id
    ]
    if strays:
        raise InvoiceNotReadyError(
            f"Line {', '.join(strays)} is not part of {order.name}. Reopen the "
            f"preview — the order has changed since it was built.",
            code="PO_LINE_MISMATCH",
        )

    # The ceiling, re-read from Odoo rather than trusted from the preview.
    problem = check_over_billing(lines, by_id)
    if problem:
        raise OverBilledError(problem)

    bill_ref = (ref or invoice.extracted_invoice_no or "").strip() or None
    bill_date = resolve_invoice_date(
        invoice_date or invoice.extracted_date, dt.date.today()
    )

    # The duplicate guard. Answers 200, not an error: the reviewer asked a
    # question and this is the true answer, with the id needed to act on it.
    if bill_ref:
        found = classify_duplicate(
            await odoo_service.find_vendor_bills(
                partner_id=order.partner_id, ref=bill_ref
            )
        )
        if found is not None:
            existing, outcome = found
            logger.warning(
                "Invoice %s: not billing — bill %s already exists for ref %r (%s)",
                invoice.id,
                existing.id,
                bill_ref,
                outcome.value,
            )

            # The bill is not created again, but the document is still put on
            # it. This branch is reached by the reviewer who clicked twice, or
            # whose first attempt created the bill and then failed — exactly the
            # cases where the scan never made it, and where returning "already
            # exists" and nothing else leaves them uploading it by hand. The
            # attach is idempotent by file name, so a bill that already has it
            # is left alone.
            attached = AttachmentStatus.SKIPPED
            if attach_document:
                document = await read_source_document(invoice)
                if document is not None:
                    status, _ = await odoo_service.attach_document(
                        res_model="account.move",
                        res_id=existing.id,
                        attachment=document,
                    )
                    attached = (
                        AttachmentStatus(status)
                        if status in {s.value for s in AttachmentStatus}
                        else AttachmentStatus.SKIPPED
                    )

            return invoice, {
                "status": outcome,
                "bill_id": existing.id,
                "bill_ref": existing.ref or existing.name,
                "attachment_status": attached,
                "invoice_date": bill_date,
            }

    quantities = {
        int(line["po_line_id"]): float(line["quantity"]) for line in lines
    }

    # Fetched BEFORE the receipt, deliberately. It cannot fail the request, but
    # it can be slow — a 10 MB scan off object storage — and anything slow
    # sitting between the irreversible receipt and the bill widens the one
    # window a retry cannot heal: goods marked received with nothing billed.
    attachment: OdooAttachment | None = None
    if attach_document:
        attachment = await read_source_document(invoice)

    # ------------------------------------------------- the irreversible write
    receipt = None
    if receive_goods:
        receipt = await odoo_service.receive_purchase_order_lines(
            po_id=order.id, quantities=quantities
        )

    created = await odoo_service.create_vendor_bill(
        po_id=order.id,
        quantities=quantities,
        vendor_ref=bill_ref,
        invoice_date=bill_date.isoformat(),
        attachment=attachment,
    )

    attachment_status = (
        AttachmentStatus(created.attachment_status)
        if created.attachment_status in {s.value for s in AttachmentStatus}
        else AttachmentStatus.SKIPPED
    )

    now = dt.datetime.now(dt.UTC)
    repo = MatchHistoryRepository(db)
    await repo.update(
        invoice,
        status=InvoiceStatus.PUSHED,
        pushed_to_odoo=True,
        pushed_at=now,
        odoo_bill_id=created.id,
        final_po_id=order.id,
        reviewed_by=reviewer_id,
        reviewed_at=now,
        # A NEW dict, not a mutation. JSONB is not mutation-tracked by
        # SQLAlchemy, so `invoice.extra["odoo_bill"] = ...` flushes nothing and
        # the audit record silently never lands.
        extra={
            **(invoice.extra or {}),
            "odoo_bill": {
                "id": created.id,
                "name": created.name,
                "display_name": created.display_name,
                "vendor_ref": bill_ref,
                "po_id": order.id,
                "po_name": order.name,
                "partner_id": order.partner_id,
                "invoice_date": bill_date.isoformat(),
                "amount_total": created.amount_total,
                "attachment": attachment_status.value,
                "receipt": receipt.picking_name if receipt else None,
                "backorders": list(receipt.backorder_names) if receipt else [],
                "created_at": now.isoformat(),
                "created_by": str(reviewer_id),
                # The mapping ACTUALLY used, not the one proposed. This is the
                # only record of which order line each quantity landed on, and
                # it is what makes an under-billed invoice arguable later.
                "lines": [
                    {
                        "po_line_id": po_line_id,
                        "quantity": quantity,
                        "description": (
                            by_id[po_line_id].product_name or by_id[po_line_id].name
                        ),
                    }
                    for po_line_id, quantity in sorted(quantities.items())
                ],
            },
        },
    )

    if invoice.uploaded_by:
        await NotificationService(db).notify_user(
            user_id=invoice.uploaded_by,
            type=NotificationType.INVOICE_PUSHED,
            title=f"{invoice.file_name} was billed in Odoo",
            message=f"{created.display_name} was created against {order.name}.",
            match_history_id=invoice.id,
            company_id=invoice.company_id,
            tenant_id=invoice.tenant_id,
        )

    await db.commit()
    logger.info(
        "Invoice %s: billed %s (%s) against %s with %d line(s) by %s "
        "(receipt=%s, attachment=%s)",
        invoice.id,
        created.display_name,
        created.id,
        order.name,
        len(quantities),
        reviewer_id,
        receipt.picking_name if receipt else "skipped",
        attachment_status.value,
    )
    return invoice, {
        "status": BillOutcome.BILL_CREATED,
        "bill_id": created.id,
        "bill_ref": created.display_name,
        "attachment_status": attachment_status,
        "invoice_date": bill_date,
        "receipt_name": receipt.picking_name if receipt else None,
        "backorder_names": list(receipt.backorder_names) if receipt else [],
    }
