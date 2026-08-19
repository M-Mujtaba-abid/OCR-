"""Create a purchase order in Odoo from what was read off an invoice.

The counterpart to `match_service`: that one finds an order that already
exists, this one offers to create the order that does not. Same inputs, same
extraction, and deliberately the same scoring — no LLM is involved here at all,
so creating a purchase order costs nothing per invoice.

Where it differs from matching is who decides. Matching produces a suggestion a
reviewer accepts or overrides on screen; this writes a record into the ERP. So
the resolution here is split in two, and the split is not arbitrary — it is
what the data supports:

  * **Vendors resolve.** Against this Odoo, every real vendor name scored 100
    with a 15-31 point margin over the runner-up, and an OCR mangling of one
    ("AJK Retardant" for "AJK Restaurants") topped out at 32. That is a signal
    strong enough to act on, and strong enough to refuse on.

  * **Products do not.** The catalogue carries near-identical variants — Lemon,
    Sanitized lemon, Lemon Leaves; Eggplant, Egg Plant Seedless, Baby Eggplant
    — and an invoice line saying "J5 (lemon)" cannot distinguish them. Measured
    on real documents, one line in five resolved correctly and one resolved
    *confidently wrong*: "Egg Plant (C. Int.)" picks "Egg Plant Seedless" at 75
    with a 20-point margin, while the correct product ranks third at 51.

So products are proposed and never decided. Every candidate goes to the
reviewer with its score, and nothing is created until a person has chosen.
"""

from __future__ import annotations

import datetime as dt
import uuid

from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvoiceNotReadyError
from app.lib.logging import get_logger
from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.notification import NotificationType
from app.repositories.match_history_repository import MatchHistoryRepository
from app.schemas.extraction import InvoiceExtraction
from app.schemas.odoo import OdooEntityMatch
from app.services.matching_engine import normalise_vendor
from app.services.notification_service import NotificationService
from app.services.odoo_service import OdooService, odoo_for_invoice
from app.services.source_document import read_source_document

logger = get_logger(__name__)

#: Shortest token worth searching on. Two characters match half the catalogue
#: and cost a wide read for nothing.
MIN_TOKEN = 3

#: A vendor is only resolved when it is this similar AND this far ahead of the
#: runner-up. 75 is the threshold `matching_engine` already uses for line
#: descriptions; the margin is what stops "A J K Restaurants Management" being
#: taken for "Gvn Restaurants Management", which scores 91.7 against it.
VENDOR_FLOOR = 75.0
VENDOR_MARGIN = 15.0

#: A product is preselected only when the answer is not really a choice. Set
#: from the measured data: "Assorted Flower" clears it at 100 against 58,
#: "Egg Plant (C. Int.)" does not at 75 against 55 — which is the case that
#: must reach a human, because its confident answer is the wrong one.
PRODUCT_PRESELECT_FLOOR = 90.0
PRODUCT_PRESELECT_MARGIN = 25.0

#: Candidates offered per line. Enough to hold the right answer; short enough
#: to read in a dropdown without scrolling.
PRODUCT_CANDIDATES = 5


def _tokens(text: str) -> list[str]:
    """Searchable tokens from free text, normalised the way scoring is.

    `normalise_vendor` does the work that makes this possible — casefolding,
    dropping punctuation and legal-form words, joining spaced initialisms — so
    "AJK Restaurants" and "A J K Restaurants Management Llc" reduce to tokens
    that overlap.
    """
    return [word for word in normalise_vendor(text).split() if len(word) >= MIN_TOKEN]


def _rank(text: str, rows: list[dict[str, object]]) -> list[OdooEntityMatch]:
    """Score Odoo records against the extracted text, best first."""
    needle = normalise_vendor(text)
    scored = [
        OdooEntityMatch(
            id=int(row["id"]),  # type: ignore[arg-type]
            name=str(row["display_name"]),
            score=float(fuzz.token_set_ratio(needle, normalise_vendor(str(row["display_name"])))),
        )
        for row in rows
    ]
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored


async def resolve_vendor(
    odoo: OdooService, name: str | None
) -> OdooEntityMatch | None:
    """The one Odoo partner this vendor name means, or nothing.

    Nothing, rather than a best guess: a purchase order raised against the
    wrong vendor is a real accounting error, and the reviewer can see the name
    that failed and fix it in Odoo or on the document.
    """
    if not name:
        return None

    rows = await odoo.search_by_tokens("res.partner", _tokens(name))
    ranked = _rank(name, rows)
    if not ranked or ranked[0].score < VENDOR_FLOOR:
        return None

    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    if ranked[0].score - runner_up < VENDOR_MARGIN:
        logger.info(
            "Vendor %r not resolved: %s (%.0f) too close to %s (%.0f)",
            name,
            ranked[0].name,
            ranked[0].score,
            ranked[1].name,
            runner_up,
        )
        return None
    return ranked[0]


async def product_candidates(
    odoo: OdooService, name: str
) -> list[OdooEntityMatch]:
    """The products this line might mean, best first. Never a verdict."""
    rows = await odoo.search_by_tokens("product.product", _tokens(name))
    return _rank(name, rows)[:PRODUCT_CANDIDATES]


def _preselect(candidates: list[OdooEntityMatch]) -> int | None:
    """The candidate the reviewer would only be confirming, if there is one."""
    if not candidates or candidates[0].score < PRODUCT_PRESELECT_FLOOR:
        return None
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    if candidates[0].score - runner_up < PRODUCT_PRESELECT_MARGIN:
        return None
    return candidates[0].id


async def build_preview(
    odoo: OdooService, invoice: MatchHistory
) -> dict[str, object]:
    """Everything the reviewer needs to approve a creation, and nothing more.

    Returned as a plain dict and validated by the API schema at the boundary,
    the way the rest of the services here hand data upwards.
    """
    if not invoice.extracted_json:
        raise InvoiceNotReadyError("This invoice has not been read yet.")

    extraction = InvoiceExtraction.model_validate(invoice.extracted_json)
    vendor = await resolve_vendor(odoo, extraction.vendor_name)

    lines: list[dict[str, object]] = []
    for index, item in enumerate(extraction.items, start=1):
        candidates = await product_candidates(odoo, item.name)
        lines.append(
            {
                "line_no": index,
                "description": item.name,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "subtotal": item.subtotal,
                "candidates": [c.model_dump() for c in candidates],
                "preselected_product_id": _preselect(candidates),
            }
        )

    return {
        "vendor_name": extraction.vendor_name,
        "vendor": vendor.model_dump() if vendor else None,
        "order_date": extraction.order_date,
        "currency": extraction.currency,
        "lines": lines,
    }


async def create_po_for_invoice(
    db: AsyncSession,
    *,
    invoice: MatchHistory,
    partner_id: int,
    order_date: str | None,
    lines: list[dict[str, object]],
    reviewer_id: uuid.UUID,
) -> MatchHistory:
    """Create the draft order in Odoo and record it against the invoice.

    Every id is re-read from Odoo first. The preview the reviewer approved may
    be minutes old, and a product archived in between would otherwise surface
    as a raw Odoo fault at creation time — unreadable, and after a partial
    write rather than before one.
    """
    if not invoice.extracted_json:
        raise InvoiceNotReadyError("This invoice has not been read yet.")
    if not lines:
        raise InvoiceNotReadyError("A purchase order needs at least one line.")

    missing = [
        str(line.get("line_no") or index)
        for index, line in enumerate(lines, start=1)
        if not line.get("product_id")
    ]
    if missing:
        raise InvoiceNotReadyError(
            f"Line {', '.join(missing)} has no Odoo product chosen. Pick one for "
            f"every line before creating the order."
        )

    odoo = await odoo_for_invoice(db, invoice)

    partners = await odoo.read_names("res.partner", [partner_id])
    if partner_id not in partners:
        raise InvoiceNotReadyError(
            f"Vendor {partner_id} no longer exists in Odoo.", code="PARTNER_NOT_FOUND"
        )

    product_ids = [int(line["product_id"]) for line in lines]  # type: ignore[arg-type]
    products = await odoo.read_names("product.product", product_ids)
    gone = [str(pid) for pid in product_ids if pid not in products]
    if gone:
        raise InvoiceNotReadyError(
            f"Product {', '.join(gone)} no longer exists in Odoo. Reopen the "
            f"preview to pick again.",
            code="PRODUCT_NOT_FOUND",
        )

    # Read before the write, and never allowed to fail it. This is the document
    # the whole order was derived from: without it the person confirming the RFQ
    # in Odoo has only figures to check, and a bill raised from the order inside
    # Odoo inherits nothing — which is how the same PDF ends up being uploaded
    # by hand.
    attachment = await read_source_document(invoice)

    created = await odoo.create_draft_purchase_order(
        partner_id=partner_id,
        date_order=_as_odoo_datetime(order_date),
        attachment=attachment,
        order_lines=[
            {
                "product_id": int(line["product_id"]),  # type: ignore[arg-type]
                # The vendor's own wording, kept: it is what makes the Odoo
                # line recognisable against the paper it came from.
                "name": str(line.get("description") or products[int(line["product_id"])])[:512],  # type: ignore[arg-type]
                "product_qty": float(line.get("quantity") or 0.0),
                "price_unit": float(line.get("unit_price") or 0.0),
            }
            for line in lines
        ],
    )

    repo = MatchHistoryRepository(db)
    await repo.update(
        invoice,
        status=InvoiceStatus.PO_CREATED,
        matched_po_id=created.id,
        matched_po_name=created.name,
        final_po_id=created.id,
        match_strategy="auto_created_po",
        match_reasoning=(
            f"No existing order matched, so {created.name} was created in Odoo "
            f"as a draft RFQ from this invoice by a reviewer."
        ),
        reviewed_by=reviewer_id,
        reviewed_at=dt.datetime.now(dt.UTC),
        # A NEW dict, not a mutation: JSONB is not mutation-tracked, so
        # `invoice.extra[...] = ...` flushes nothing and the record silently
        # never lands. Same reason it is written this way for bills.
        extra={
            **(invoice.extra or {}),
            "odoo_po": {
                "id": created.id,
                "name": created.name,
                "attachment": created.attachment_status,
                "attachment_id": created.attachment_id,
            },
        },
    )

    if created.attachment_status not in {"attached", "none"}:
        # Said out loud rather than swallowed. The order is fine; the document
        # is not on it, and the person confirming it in Odoo is the one who
        # will discover that at the worst moment.
        logger.warning(
            "Invoice %s: %s was created but the scan is %s",
            invoice.id,
            created.name,
            created.attachment_status,
        )

    if invoice.uploaded_by:
        await NotificationService(db).notify_user(
            user_id=invoice.uploaded_by,
            type=NotificationType.INVOICE_CONFIRMED,
            title=f"{invoice.file_name} became a purchase order",
            message=f"{created.name} was created in Odoo as a draft.",
            match_history_id=invoice.id,
            company_id=invoice.company_id,
        )

    await db.commit()
    logger.info(
        "Invoice %s: created %s (%s) in Odoo by %s, attachment=%s",
        invoice.id,
        created.name,
        created.id,
        reviewer_id,
        created.attachment_status,
    )
    return invoice


def _as_odoo_datetime(value: str | None) -> str:
    """Odoo's `date_order` is a datetime; extraction gives a date or nothing."""
    if value:
        try:
            return f"{dt.date.fromisoformat(value[:10]).isoformat()} 00:00:00"
        except ValueError:
            pass
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
