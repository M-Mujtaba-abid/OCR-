"""Invoice controller: HTTP in, HTTP out."""

from __future__ import annotations

import math
import uuid

from fastapi import BackgroundTasks, UploadFile

from app.core.exceptions import InvoiceNotReadyError
from app.lib.responses import ApiResponse, PaginatedData, PaginationMeta
from app.models.match_history import InvoiceStatus
from app.models.user import User
from app.core.config import settings
from app.schemas.invoice import (
    ConfirmMatchRequest,
    CreatePoRequest,
    FileLink,
    InvoiceDetail,
    InvoiceListItem,
    InvoiceStats,
    InvoiceTrend,
    JobAccepted,
    PoPreview,
    RejectInvoiceRequest,
    UploadResult,
)
from app.services.invoice_service import InvoiceService
from app.services.match_service import (
    confirm_match,
    reject_invoice,
    run_matching_for_invoice,
)
from app.services.ocr_service import run_ocr_for_invoice
from app.services.po_creator_service import build_preview, create_po_for_invoice


def _page(items: list[InvoiceListItem], page: int, page_size: int, total: int):
    return PaginatedData[InvoiceListItem](
        items=items,
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            pages=max(1, math.ceil(total / page_size)),
        ),
    )


class InvoiceController:
    def __init__(self, service: InvoiceService) -> None:
        self.service = service

    async def upload(
        self,
        *,
        user: User,
        files: list[UploadFile],
        member_ref_no: str | None,
        member_notes: str | None,
        background: BackgroundTasks,
    ) -> ApiResponse[UploadResult]:
        created, rejected = await self.service.upload_invoices(
            user=user,
            files=files,
            member_ref_no=member_ref_no,
            member_notes=member_notes,
        )

        # Queued, not awaited. Mistral takes 5–20 seconds per document and the
        # member has no reason to sit through it — they get a 201 immediately
        # and the row moves to ocr_done on its own while the UI polls.
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

    async def list_all(
        self,
        *,
        page: int,
        page_size: int,
        status: InvoiceStatus | None,
        open_only: bool,
        uploaded_by: uuid.UUID | None,
    ) -> ApiResponse[PaginatedData[InvoiceListItem]]:
        items, total = await self.service.list_all(
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
        self, *, user: User | None
    ) -> ApiResponse[InvoiceStats]:
        return ApiResponse.ok(
            await self.service.get_stats(user=user), message="Stats retrieved"
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

    async def trend(self, *, days: int) -> ApiResponse[InvoiceTrend]:
        return ApiResponse.ok(await self.service.trend(days=days))

    async def po_preview(
        self, *, invoice_id: uuid.UUID, user: User
    ) -> ApiResponse[PoPreview]:
        """What creating a purchase order from this invoice would produce."""
        invoice = await self.service.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True
        )
        preview = await build_preview(invoice)
        return ApiResponse.ok(
            PoPreview.model_validate({**preview, "odoo_url": settings.odoo_base_url})
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
