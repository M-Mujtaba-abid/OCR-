"""Invoice controller: HTTP in, HTTP out."""

from __future__ import annotations

import math
import uuid

from fastapi import BackgroundTasks, UploadFile

from app.core.exceptions import AppError, InvoiceNotReadyError
from app.lib.responses import ApiResponse, PaginatedData, PaginationMeta
from app.models.company import Company
from app.models.match_history import InvoiceStatus
from app.models.user import User
from app.schemas.invoice import (
    BillHistoryItem,
    BillOutcome,
    BillPreview,
    ConfirmMatchRequest,
    CreateBillRequest,
    CreateBillResult,
    CreatePoRequest,
    FileLink,
    InvoiceDetail,
    InvoiceListItem,
    InvoiceStats,
    InvoiceTrend,
    JobAccepted,
    PoPreview,
    RegisterUploadsRequest,
    RejectInvoiceRequest,
    UploadResult,
    UploadTicket,
    UploadTicketsRequest,
)
from app.services.invoice_service import InvoiceService
from app.services.match_service import (
    confirm_match,
    reject_invoice,
    run_matching_for_invoice,
)
from app.services.bill_creator_service import (
    bill_history_item,
    bill_url,
    build_bill_preview,
    create_bill_for_invoice,
)
from app.services.ocr_service import run_ocr_for_invoice
from app.services.odoo_service import odoo_for, odoo_for_invoice
from app.services.po_creator_service import build_preview, create_po_for_invoice


def _meta(page: int, page_size: int, total: int) -> PaginationMeta:
    return PaginationMeta(
        page=page,
        page_size=page_size,
        total=total,
        pages=max(1, math.ceil(total / page_size)),
    )


def _page(items: list[InvoiceListItem], page: int, page_size: int, total: int):
    return PaginatedData[InvoiceListItem](
        items=items, pagination=_meta(page, page_size, total)
    )


class InvoiceController:
    def __init__(self, service: InvoiceService) -> None:
        self.service = service

    async def upload(
        self,
        *,
        user: User,
        company: Company,
        files: list[UploadFile],
        member_ref_no: str | None,
        member_notes: str | None,
        background: BackgroundTasks,
    ) -> ApiResponse[UploadResult]:
        created, rejected = await self.service.upload_invoices(
            user=user,
            company=company,
            files=files,
            member_ref_no=member_ref_no,
            member_notes=member_notes,
        )

        # A no-op unless OCR_IN_UPLOAD_REQUEST is on. By default the client
        # starts each invoice with its own call, because a background task
        # queued here runs inside the invocation this response is waiting on —
        # see the setting's own note. Either way the member gets a 201 and the
        # rows move on their own while the UI polls.
        self.service.schedule_extraction(background, created)

        # 201 even with rejections: at least one invoice was created, and the
        # rejected list tells the client exactly what did not make it.
        message = f"{len(created)} invoice{'s' if len(created) != 1 else ''} uploaded"
        if rejected:
            message += f", {len(rejected)} rejected"

        return ApiResponse.ok(
            UploadResult(
                uploaded=[InvoiceListItem.model_validate(i) for i in created],
                rejected=rejected,
            ),
            message=message,
        )

    async def list_own(
        self, *, user: User, page: int, page_size: int, status: InvoiceStatus | None
    ) -> ApiResponse[PaginatedData[InvoiceListItem]]:
        items, total = await self.service.list_own(
            user=user, page=page, page_size=page_size, status=status
        )
        return ApiResponse.ok(
            _page(
                [InvoiceListItem.model_validate(i) for i in items],
                page,
                page_size,
                total,
            ),
            message="Invoices retrieved",
        )

    async def _odoo_url(self, company_id: uuid.UUID) -> str:
        """This company's Odoo base URL, for building deep links.

        Never raises: a company with no Odoo configured still has a bill
        history to read, and an empty string is what tells the client to render
        the reference without a link rather than a link that goes nowhere.
        """
        try:
            odoo = await odoo_for(self.service.db, company_id)
        except AppError:
            return ""
        return odoo._credentials.base_url

    async def list_all(
        self,
        *,
        company_id: uuid.UUID,
        page: int,
        page_size: int,
        status: InvoiceStatus | None,
        open_only: bool,
        uploaded_by: uuid.UUID | None,
    ) -> ApiResponse[PaginatedData[InvoiceListItem]]:
        items, total = await self.service.list_all(
            company_id=company_id,
            page=page,
            page_size=page_size,
            status=status,
            open_only=open_only,
            uploaded_by=uploaded_by,
        )
        return ApiResponse.ok(
            _page(
                [InvoiceListItem.model_validate(i) for i in items],
                page,
                page_size,
                total,
            ),
            message="Invoices retrieved",
        )

    async def bill_history(
        self,
        *,
        company_id: uuid.UUID,
        page: int,
        page_size: int,
        uploaded_by: uuid.UUID | None,
    ) -> ApiResponse[PaginatedData[BillHistoryItem]]:
        """Every bill this system raised, newest first.

        The rows are read straight out of what was recorded at creation time —
        no Odoo call — so this answers just as well when Odoo is down. The deep
        links are built here, from one server-side template, because the URL
        shape is Odoo-version-specific and a client that builds its own is a
        second copy to forget.
        """
        items, total = await self.service.list_billed(
            company_id=company_id,
            page=page,
            page_size=page_size,
            uploaded_by=uploaded_by,
        )
        # Resolved ONCE for the page, not per row: every invoice here belongs
        # to the same company by construction, and the alternative is a
        # credential lookup for each of twenty rows.
        odoo_url = await self._odoo_url(company_id)

        return ApiResponse.ok(
            PaginatedData[BillHistoryItem](
                items=[
                    BillHistoryItem.model_validate(
                        bill_history_item(i, odoo_url=odoo_url)
                    )
                    for i in items
                ],
                pagination=_meta(page, page_size, total),
            ),
            message="Bill history retrieved",
        )

    async def get(
        self, *, invoice_id: uuid.UUID, user: User, can_read_all: bool
    ) -> ApiResponse[InvoiceDetail]:
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id,
            user=user,
            can_read_all=can_read_all,
            # The detail view renders the line items; the list view never does.
            with_lines=True,
        )
        return ApiResponse.ok(
            InvoiceDetail.model_validate(invoice), message="Invoice retrieved"
        )

    async def file_link(
        self, *, invoice_id: uuid.UUID, user: User, can_read_all: bool
    ) -> ApiResponse[FileLink]:
        invoice, url, ttl = await self.service.get_download_url(
            invoice_id=invoice_id, user=user, can_read_all=can_read_all
        )
        return ApiResponse.ok(
            FileLink(
                url=url,
                expires_in=ttl,
                file_name=invoice.file_name,
                mime_type=invoice.mime_type,
            ),
            message="Download link generated",
        )

    async def stats(
        self, *, company_id: uuid.UUID, user: User | None
    ) -> ApiResponse[InvoiceStats]:
        return ApiResponse.ok(
            await self.service.get_stats(company_id=company_id, user=user),
            message="Stats retrieved",
        )

    async def delete(
        self, *, invoice_id: uuid.UUID, actor: User
    ) -> ApiResponse[None]:
        await self.service.delete(invoice_id=invoice_id, actor=actor)
        return ApiResponse.ok(None, message="Invoice deleted")

    # ------------------------------------------------------------- pipeline
    async def start_ocr(
        self, *, invoice_id: uuid.UUID, user: User, background: BackgroundTasks
    ) -> ApiResponse[JobAccepted]:
        """Queue extraction. Returns immediately — Mistral takes 5-20 seconds."""
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True
        )
        if invoice.status is InvoiceStatus.OCR_PROCESSING:
            raise InvoiceNotReadyError("This invoice is already being read.")

        # Claim the row here, in the request, rather than leaving it to the
        # background task. Otherwise the 202 announces "ocr_queued" while the
        # database still says ocr_done, and a client that polls immediately
        # sees the OLD terminal status and concludes the work already finished.
        await self.service.mark_status(invoice, InvoiceStatus.OCR_QUEUED)

        background.add_task(run_ocr_for_invoice, invoice.id)
        return ApiResponse.ok(
            JobAccepted(
                id=invoice.id,
                status=InvoiceStatus.OCR_QUEUED,
                message="Extraction started.",
            ),
            message="Extraction queued",
        )

    async def start_upload_extraction(
        self, *, invoice_id: uuid.UUID, user: User, background: BackgroundTasks
    ) -> ApiResponse[JobAccepted]:
        """Start extraction for an invoice the caller has just uploaded.

        The member-facing twin of `start_ocr`, and deliberately narrower on
        every axis, because this one is reachable by anybody who can upload:

          * Only the uploader. `can_read_all=False`, so somebody else's id is a
            404 exactly as it is everywhere else.
          * Only from `uploaded`. Anything further along is answered with the
            status it already holds rather than an error — a double click, a
            retry, or a second tab is then harmless, and a member cannot re-run
            extraction on a finished invoice and spend Mistral budget doing it.
          * Only when extraction is switched on at all, so a client call cannot
            walk around the kill switch.

        This exists because the upload response no longer queues extraction
        itself: on a serverless platform that background task runs inside the
        invocation the browser is waiting on. One call per invoice moves the
        work into its own invocation, which returns the upload immediately and
        extracts every file in parallel rather than one after another.
        """
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=False
        )

        if (
            invoice.status is not InvoiceStatus.UPLOADED
            or not InvoiceService.extraction_enabled()
        ):
            # Not an error, and deliberately not a 409: the caller asked for
            # this invoice to be moving, and it either is or is finished. The
            # status it comes back with says which.
            return ApiResponse.ok(
                JobAccepted(
                    id=invoice.id,
                    status=invoice.status,
                    message="Nothing to start.",
                ),
                message="Already started",
            )

        # Claimed here, in the request, for the same reason `start_ocr` does
        # it: the 202 must not announce "queued" while the row still reads
        # `uploaded` to a client that polls immediately.
        await self.service.mark_status(invoice, InvoiceStatus.OCR_QUEUED)

        background.add_task(run_ocr_for_invoice, invoice.id)
        return ApiResponse.ok(
            JobAccepted(
                id=invoice.id,
                status=InvoiceStatus.OCR_QUEUED,
                message="Extraction started.",
            ),
            message="Extraction queued",
        )

    async def start_matching(
        self, *, invoice_id: uuid.UUID, user: User, background: BackgroundTasks
    ) -> ApiResponse[JobAccepted]:
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True
        )
        if not invoice.extracted_json:
            # A clear 409 rather than a background task that fails silently and
            # leaves the admin watching a spinner.
            raise InvoiceNotReadyError(
                "This invoice has not been read yet. Run extraction first."
            )
        if invoice.status is InvoiceStatus.MATCHING:
            raise InvoiceNotReadyError("This invoice is already being matched.")

        # Same reasoning as start_ocr: claim the row before answering, so the
        # status the client polls for is already true when it polls.
        await self.service.mark_status(invoice, InvoiceStatus.MATCHING)

        background.add_task(run_matching_for_invoice, invoice.id)
        return ApiResponse.ok(
            JobAccepted(
                id=invoice.id,
                status=InvoiceStatus.MATCHING,
                message="Matching started.",
            ),
            message="Matching queued",
        )

    async def confirm(
        self, *, invoice_id: uuid.UUID, user: User, payload: ConfirmMatchRequest
    ) -> ApiResponse[InvoiceDetail]:
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True, with_lines=True
        )
        updated = await confirm_match(
            self.service.db, invoice=invoice, po_id=payload.po_id, reviewer_id=user.id
        )
        return ApiResponse.ok(
            InvoiceDetail.model_validate(updated),
            message=(
                "Match corrected" if updated.was_corrected else "Match confirmed"
            ),
        )

    async def upload_tickets(
        self, *, company: Company, payload: UploadTicketsRequest
    ) -> ApiResponse[list[UploadTicket]]:
        """Signed URLs the browser uploads to directly."""
        return ApiResponse.ok(
            await self.service.issue_upload_tickets(
                company=company, files=payload.files
            )
        )

    async def register_uploads(
        self,
        *,
        user: User,
        company: Company,
        payload: RegisterUploadsRequest,
        background: BackgroundTasks,
    ) -> ApiResponse[UploadResult]:
        created, rejected = await self.service.register_uploads(
            user=user,
            company=company,
            files=payload.files,
            member_ref_no=payload.member_ref_no,
            member_notes=payload.member_notes,
        )
        # As in `upload`: a no-op unless OCR_IN_UPLOAD_REQUEST is on, because
        # the member must not wait for Mistral.
        self.service.schedule_extraction(background, created)
        return ApiResponse.ok(
            UploadResult(
                uploaded=[InvoiceListItem.model_validate(i) for i in created],
                rejected=rejected,
            ),
            message=f"{len(created)} invoice(s) uploaded",
        )

    async def trend(
        self, *, company_id: uuid.UUID, days: int
    ) -> ApiResponse[InvoiceTrend]:
        return ApiResponse.ok(
            await self.service.trend(company_id=company_id, days=days)
        )

    async def po_preview(
        self, *, invoice_id: uuid.UUID, user: User
    ) -> ApiResponse[PoPreview]:
        """What creating a purchase order from this invoice would produce."""
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True
        )
        odoo = await odoo_for_invoice(self.service.db, invoice)
        preview = await build_preview(odoo, invoice)
        # The company's own Odoo URL, so the deep links on the screen point at
        # the deployment the preview was actually read from.
        return ApiResponse.ok(
            PoPreview.model_validate(
                {**preview, "odoo_url": odoo._credentials.base_url}
            )
        )

    async def create_po(
        self, *, invoice_id: uuid.UUID, user: User, payload: CreatePoRequest
    ) -> ApiResponse[InvoiceDetail]:
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True, with_lines=True
        )
        updated = await create_po_for_invoice(
            self.service.db,
            invoice=invoice,
            partner_id=payload.partner_id,
            order_date=payload.order_date,
            lines=[line.model_dump() for line in payload.lines],
            reviewer_id=user.id,
        )
        return ApiResponse.ok(
            InvoiceDetail.model_validate(updated),
            message=f"Created {updated.matched_po_name} in Odoo as a draft",
        )

    async def bill_preview(
        self, *, invoice_id: uuid.UUID, user: User
    ) -> ApiResponse[BillPreview]:
        """What billing this invoice against its matched order would produce."""
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True
        )
        odoo = await odoo_for_invoice(self.service.db, invoice)
        return ApiResponse.ok(
            BillPreview.model_validate(await build_bill_preview(odoo, invoice))
        )

    async def create_bill(
        self, *, invoice_id: uuid.UUID, user: User, payload: CreateBillRequest
    ) -> ApiResponse[CreateBillResult]:
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True, with_lines=True
        )
        updated, outcome = await create_bill_for_invoice(
            self.service.db,
            invoice=invoice,
            po_id=payload.po_id,
            ref=payload.ref,
            invoice_date=payload.invoice_date,
            lines=[line.model_dump() for line in payload.lines],
            receive_goods=payload.receive_goods,
            attach_document=payload.attach_document,
            reviewer_id=user.id,
        )
        # Three outcomes, three messages. A blanket "Bill created" on a request
        # that created nothing is the kind of confirmation that gets a vendor
        # paid twice by somebody who trusted it.
        message = {
            BillOutcome.BILL_CREATED: f"Created {outcome['bill_ref']} in Odoo as a draft",
            BillOutcome.BILL_EXISTS: f"{outcome['bill_ref']} already exists in Odoo",
            BillOutcome.ALREADY_PAID: (
                f"{outcome['bill_ref']} already exists in Odoo and is paid"
            ),
        }[outcome["status"]]

        return ApiResponse.ok(
            CreateBillResult.model_validate(
                {
                    **outcome,
                    "bill_url": bill_url(
                        await self._odoo_url(invoice.company_id),
                        outcome.get("bill_id"),
                    ),
                    "invoice": InvoiceDetail.model_validate(updated),
                }
            ),
            message=message,
        )

    async def reject(
        self, *, invoice_id: uuid.UUID, user: User, payload: RejectInvoiceRequest
    ) -> ApiResponse[InvoiceDetail]:
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True, with_lines=True
        )
        updated = await reject_invoice(
            self.service.db, invoice=invoice, reason=payload.reason, reviewer_id=user.id
        )
        return ApiResponse.ok(
            InvoiceDetail.model_validate(updated), message="Invoice rejected"
        )
