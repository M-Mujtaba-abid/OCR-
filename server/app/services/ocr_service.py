"""Read an uploaded invoice with Mistral and persist what it says.

Runs as a background task, so it takes an invoice id rather than an ORM object
and opens its own database session. `get_db` is request-scoped and closes when
the response is sent; a task holding that session fails on its first query.

Every failure path ends with a status the UI can render. An unhandled exception
here would leave a row stuck in `ocr_processing` forever with nothing to explain
why, which is the worst possible outcome for a queue somebody is watching.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import mistral, storage
from app.core.config import settings
from app.core.exceptions import AppError
from app.db.session import SessionFactory
from app.lib.logging import get_logger
from app.models.invoice_line_match import InvoiceLineMatch, LineMatchStatus
from app.models.match_history import InvoiceStatus, MatchHistory
from app.repositories.match_history_repository import MatchHistoryRepository
from app.schemas.extraction import DocumentExtraction, InvoiceExtraction

logger = get_logger(__name__)

#: The extraction brief. The field-by-field rules live in the JSON schema as
#: `description` text (see schemas/extraction.py); this covers only what a
#: schema cannot express — what the document is and how to treat absences.
EXTRACTION_PROMPT = """\
You are reading a document that contains one or more vendor invoices, bills or
purchase orders.

Extract into the provided JSON schema.

HOW MANY INVOICES
- Return one entry in `invoices` for EVERY separate document in the file.
- A new invoice begins where a different document number, a different vendor,
  or a second totals block appears. Batch scans and ERP exports routinely put
  several unrelated invoices in one PDF.
- A single invoice whose item table continues onto the next page is still ONE
  invoice. Do not split it, and do not merge two invoices into one.

FIELDS
- Every field in the schema must appear in your answer. Use null for a string
  that is genuinely absent and 0.0 for an absent number. Never omit a key and
  never invent a value.
- `po_number` is whichever reference the document leads with: a purchase order
  number, an invoice number, or a document ID.
- Include every row of each item table, in the order printed.
- Capture `product_code` when a line carries a SKU, article number or product
  ID — including one written inside the description, such as
  "Product ID: 4426" or a bracketed code like "[AVO-01]".
- Capture `uom` when a unit is printed: kg, pcs, box, ltr, carton.

LANGUAGE
- Transcribe text in its ORIGINAL language and script. Arabic, Urdu, Chinese
  and Cyrillic descriptions and vendor names must be preserved as written.
  Never translate and never transliterate — the value has to match what is
  stored in the ERP, which is the original.
- Numbers are the exception: convert Eastern Arabic numerals (٠١٢٣) and any
  other localised digits to Western digits, and use a period as the decimal
  separator regardless of what the document prints.

TOTALS
- Read the totals from the totals block, not by re-adding the lines. Where the
  document and the arithmetic disagree, the document wins — that difference is
  usually a discount, and it is evidence.
"""


async def run_ocr_for_invoice(invoice_id: uuid.UUID) -> None:
    """Extract one invoice. Never raises — every outcome is a status.

    Called from a background task, where a raised exception would be logged by
    Starlette and otherwise vanish, leaving the row mid-flight.
    """
    async with SessionFactory() as db:
        repo = MatchHistoryRepository(db)
        invoice = await repo.find_by_id(invoice_id)

        if invoice is None:
            # Withdrawn between upload and the task starting. Not an error.
            logger.info("OCR skipped: invoice %s no longer exists", invoice_id)
            return

        if invoice.status not in _OCR_STARTABLE:
            logger.info(
                "OCR skipped: invoice %s is %s", invoice_id, invoice.status.value
            )
            return

        if invoice.status is not InvoiceStatus.OCR_PROCESSING:
            await repo.update(invoice, status=InvoiceStatus.OCR_PROCESSING)
            await db.commit()

        try:
            extraction, outcome = await _extract(invoice)
        except AppError as exc:
            await _fail(db, repo, invoice, exc.message)
            return
        except Exception:
            logger.exception("OCR crashed for invoice %s", invoice_id)
            await _fail(db, repo, invoice, "An unexpected error occurred while reading the document.")
            return

        await _persist(db, repo, invoice, extraction, outcome)


#: Statuses from which extraction may begin. Re-running is allowed from a
#: finished or failed state so an admin can retry; it is refused mid-flight so
#: two workers cannot write the same row.
_OCR_STARTABLE = frozenset(
    {
        InvoiceStatus.UPLOADED,
        InvoiceStatus.OCR_QUEUED,
        InvoiceStatus.OCR_FAILED,
        InvoiceStatus.OCR_DONE,
        InvoiceStatus.NO_MATCH,
        InvoiceStatus.MATCH_FAILED,
    }
)


async def _extract(
    invoice: MatchHistory,
) -> tuple[DocumentExtraction, mistral.OcrOutcome]:
    """Run the extraction. Raises AppError subclasses on failure.

    The document is handed to Mistral as a presigned R2 URL rather than being
    uploaded to Mistral's own storage. The file is already in R2, the URL is
    valid for minutes, and this avoids sending every invoice to a second vendor.
    """
    signed_url = await storage.generate_presigned_url(
        invoice.file_key, settings.OCR_SIGNED_URL_TTL
    )

    # One call: OCR and structured extraction together. The model sees the page
    # layout, which is what distinguishes a grand total from a line amount in a
    # table — information a markdown rendering has already thrown away.
    outcome = await mistral.run_ocr(
        signed_url,
        annotation_model=DocumentExtraction,
        annotation_prompt=EXTRACTION_PROMPT,
    )

    if outcome.annotation is not None:
        return mistral.validate_extraction(outcome.annotation, DocumentExtraction), outcome

    # No annotation came back. Either the document exceeded the 8-page
    # annotation cap, or the pass simply returned nothing. Fall back to
    # structuring the markdown with a chat completion — a second call, working
    # from flattened text, and measurably worse. Used only when it must be.
    logger.info(
        "No annotation for invoice %s (%d pages) — falling back to chat extraction",
        invoice.id,
        outcome.page_count,
    )
    payload = await mistral.extract_from_text(
        outcome.markdown,
        schema_model=DocumentExtraction,
        system_prompt=EXTRACTION_PROMPT,
    )
    return mistral.validate_extraction(payload, DocumentExtraction), outcome


async def _persist(
    db: AsyncSession,
    repo: MatchHistoryRepository,
    invoice: MatchHistory,
    document: DocumentExtraction,
    outcome: mistral.OcrOutcome,
) -> None:
    """Write the extraction, splitting a multi-invoice document into rows.

    The uploaded row takes the first invoice. Any others get their own row
    pointing at the SAME stored file, because everything downstream — matching,
    review, the eventual Odoo bill — assumes one row is one invoice against one
    purchase order. Keeping three invoices in one row would make every one of
    those steps ambiguous.
    """
    extraction = document.primary

    await _write_one(db, repo, invoice, extraction, outcome)

    for index, extra in enumerate(document.invoices[1:], start=2):
        sibling = await repo.create(
            tenant_id=invoice.tenant_id,
            uploaded_by=invoice.uploaded_by,
            member_ref_no=invoice.member_ref_no,
            member_notes=invoice.member_notes,
            # Same object in R2 — one upload, not copies. The file name is
            # suffixed so a queue of split rows is legible at a glance.
            file_name=f"{invoice.file_name} [{index}]",
            file_key=invoice.file_key,
            file_url=invoice.file_url,
            file_size_bytes=invoice.file_size_bytes,
            mime_type=invoice.mime_type,
            status=InvoiceStatus.UPLOADED,
            extra={"split_from": str(invoice.id), "document_index": index},
        )
        await _write_one(db, repo, sibling, extra, outcome)

    await db.commit()

    if len(document.invoices) > 1:
        logger.info(
            "Invoice %s contained %d separate invoices — split into %d rows",
            invoice.id,
            len(document.invoices),
            len(document.invoices),
        )

    logger.info(
        "OCR done for invoice %s: vendor=%r ref=%r total=%s lines=%d",
        invoice.id,
        extraction.vendor_name,
        extraction.po_number,
        extraction.total_amount,
        len(extraction.items),
    )


async def _write_one(
    db: AsyncSession,
    repo: MatchHistoryRepository,
    invoice: MatchHistory,
    extraction: InvoiceExtraction,
    outcome: mistral.OcrOutcome,
) -> None:
    """Apply one extracted invoice to one row. Does not commit."""
    await repo.update(
        invoice,
        status=InvoiceStatus.OCR_DONE,
        ocr_provider="mistral",
        ocr_model=outcome.model,
        ocr_raw=outcome.raw,
        ocr_text=outcome.markdown or None,
        ocr_completed_at=dt.datetime.now(dt.UTC),
        ocr_error=None,
        page_count=outcome.page_count or None,
        extracted_json=extraction.model_dump(mode="json"),
        extracted_vendor=extraction.vendor_name,
        extracted_invoice_no=extraction.po_number,
        extracted_date=extraction.order_date_value,
        extracted_total=extraction.total_amount or None,
        extracted_tax=extraction.tax_amount or None,
        extracted_untaxed=extraction.untaxed_amount or None,
        extracted_currency=extraction.currency,
        extracted_line_count=len(extraction.items),
    )

    # No commit here: the caller commits once, so a multi-invoice document is
    # written whole or not at all.
    await _replace_lines(db, invoice, extraction)


async def _replace_lines(
    db: AsyncSession, invoice: MatchHistory, extraction: InvoiceExtraction
) -> None:
    """Rewrite the line rows from the extraction.

    Delete-then-insert rather than an upsert: a re-run may legitimately produce
    a different number of lines, and reconciling old rows against new ones by
    position would silently attach line 3's confirmed product mapping to a
    completely different product.
    """
    from sqlalchemy import delete

    await db.execute(
        delete(InvoiceLineMatch).where(InvoiceLineMatch.match_history_id == invoice.id)
    )

    db.add_all(
        InvoiceLineMatch(
            match_history_id=invoice.id,
            line_no=index,
            raw_description=item.name[:512],
            # The SKU printed on the line. When a vendor quotes the buyer's own
            # product code this makes line matching exact rather than fuzzy.
            raw_product_code=item.product_code,
            uom=item.uom,
            quantity=item.quantity or None,
            unit_price=item.unit_price or None,
            amount=item.subtotal or None,
            # Only what the document printed against the line. Invoices that
            # state tax once at the bottom leave this null, and the review
            # screen allocates it there — where it can be labelled as derived
            # rather than stored as though it had been read off the page.
            tax_amount=item.tax or None,
            status=LineMatchStatus.PENDING,
            source="pending",
        )
        for index, item in enumerate(extraction.items, start=1)
    )
    await db.flush()


async def _fail(
    db: AsyncSession,
    repo: MatchHistoryRepository,
    invoice: MatchHistory,
    reason: str,
) -> None:
    """Record a failure the UI can show and an admin can retry from."""
    try:
        await repo.update(
            invoice,
            status=InvoiceStatus.OCR_FAILED,
            ocr_error=reason[:2000],
            ocr_completed_at=dt.datetime.now(dt.UTC),
        )
        await db.commit()
    except Exception:
        # The row cannot be updated — nothing further can be done here, and
        # raising would only lose the original reason.
        await db.rollback()
        logger.exception("Could not record OCR failure for invoice %s", invoice.id)

    logger.warning("OCR failed for invoice %s: %s", invoice.id, reason)


async def reap_stuck_invoices(older_than_minutes: int = 10) -> int:
    """Flip abandoned in-flight rows to a failed state. Run at startup.

    Background tasks do not survive a restart, so a process that dies mid-OCR
    leaves rows in `ocr_processing` with nothing to move them. Without this they
    sit there forever, and the UI polls them forever.
    """
    from sqlalchemy import update

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=older_than_minutes)

    async with SessionFactory() as db:
        result = await db.execute(
            update(MatchHistory)
            .where(
                MatchHistory.status.in_(
                    [InvoiceStatus.OCR_PROCESSING, InvoiceStatus.MATCHING]
                ),
                MatchHistory.updated_at < cutoff,
            )
            .values(
                status=InvoiceStatus.OCR_FAILED,
                ocr_error="Processing was interrupted. Please retry.",
            )
        )
        await db.commit()
        count = int(result.rowcount or 0)

    if count:
        logger.warning("Reaped %d invoice(s) stuck in processing", count)
    return count
