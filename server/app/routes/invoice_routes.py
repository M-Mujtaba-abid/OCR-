"""Invoice routes — HTTP surface only.

Two read scopes, gated by different permissions:

    invoice.read      -> your own uploads          (member, manager, admin)
    invoice.read.all  -> everybody's uploads       (manager, admin)

`/my` and `/admin/queue` are separate endpoints rather than one endpoint that
branches on role. A single endpoint returning different data to different
callers is the shape that produces "admin sees member's view" bugs, and it
makes the permission impossible to read off the route table.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Path,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.invoice_controller import InvoiceController
from app.db.session import get_db
from app.dependencies.auth import require_permission, user_permissions
from app.lib.responses import ApiErrorResponse, ApiResponse, PaginatedData
from app.models.match_history import InvoiceStatus
from app.models.user import User
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

router = APIRouter(prefix="/invoices", tags=["invoices"])


def get_invoice_controller(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InvoiceController:
    return InvoiceController(InvoiceService(db))


Controller = Annotated[InvoiceController, Depends(get_invoice_controller)]
CanCreate = Annotated[User, Depends(require_permission("invoice.create"))]
CanRead = Annotated[User, Depends(require_permission("invoice.read"))]
CanReadAll = Annotated[User, Depends(require_permission("invoice.read.all"))]
# Matching and confirming both post a decision that ends in a vendor bill, so
# they need approval rights, not merely the ability to read every invoice.
CanApprove = Annotated[User, Depends(require_permission("invoice.approve"))]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ApiErrorResponse},
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    413: {"model": ApiErrorResponse},
    415: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
    502: {"model": ApiErrorResponse},
    503: {"model": ApiErrorResponse},
}


@router.post(
    "/upload",
    response_model=ApiResponse[UploadResult],
    status_code=status.HTTP_201_CREATED,
    summary="Upload 1-10 invoice files (requires invoice.create)",
    responses=ERROR_RESPONSES,
)
async def upload_invoices(
    controller: Controller,
    user: CanCreate,
    background: BackgroundTasks,
    # `list[UploadFile]` maps to a repeated `files` part in the multipart body,
    # which is what a browser sends for <input type="file" multiple>.
    files: Annotated[list[UploadFile], File(description="PDF, PNG, JPEG or TIFF")],
    member_ref_no: Annotated[str | None, Form(max_length=120)] = None,
    member_notes: Annotated[str | None, Form(max_length=4000)] = None,
) -> ApiResponse[UploadResult]:
    return await controller.upload(
        user=user,
        files=files,
        member_ref_no=member_ref_no,
        member_notes=member_notes,
        background=background,
    )


@router.get(
    "/my",
    response_model=ApiResponse[PaginatedData[InvoiceListItem]],
    summary="Your own uploads (requires invoice.read)",
    responses=ERROR_RESPONSES,
)
async def my_invoices(
    controller: Controller,
    user: CanRead,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    invoice_status: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
) -> ApiResponse[PaginatedData[InvoiceListItem]]:
    return await controller.list_own(
        user=user, page=page, page_size=page_size, status=invoice_status
    )


@router.get(
    "/my/stats",
    response_model=ApiResponse[InvoiceStats],
    summary="Your own invoice counts (requires invoice.read)",
    responses=ERROR_RESPONSES,
)
async def my_stats(controller: Controller, user: CanRead) -> ApiResponse[InvoiceStats]:
    return await controller.stats(user=user)


@router.get(
    "/admin/queue",
    response_model=ApiResponse[PaginatedData[InvoiceListItem]],
    summary="Every uploader's invoices (requires invoice.read.all)",
    responses=ERROR_RESPONSES,
)
async def admin_queue(
    controller: Controller,
    _actor: CanReadAll,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    invoice_status: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    open_only: Annotated[bool, Query()] = False,
    uploaded_by: Annotated[uuid.UUID | None, Query()] = None,
) -> ApiResponse[PaginatedData[InvoiceListItem]]:
    return await controller.list_all(
        page=page,
        page_size=page_size,
        status=invoice_status,
        open_only=open_only,
        uploaded_by=uploaded_by,
    )


@router.get(
    "/admin/stats",
    response_model=ApiResponse[InvoiceStats],
    summary="Tenant-wide invoice counts (requires invoice.read.all)",
    responses=ERROR_RESPONSES,
)
async def admin_stats(
    controller: Controller, _actor: CanReadAll
) -> ApiResponse[InvoiceStats]:
    # Declared before /{invoice_id} so "admin" is never parsed as a UUID.
    return await controller.stats(user=None)


@router.get(
    "/admin/trend",
    response_model=ApiResponse[InvoiceTrend],
    summary="Daily arrivals and reviews (requires invoice.read.all)",
    responses=ERROR_RESPONSES,
)
async def admin_trend(
    controller: Controller,
    _actor: CanReadAll,
    days: Annotated[int, Query(ge=7, le=90)] = 14,
) -> ApiResponse[InvoiceTrend]:
    # Declared before /{invoice_id}, like /admin/stats, so "admin" is never
    # parsed as a UUID.
    return await controller.trend(days=days)


@router.get(
    "/{invoice_id}",
    response_model=ApiResponse[InvoiceDetail],
    summary="One invoice (own, or any with invoice.read.all)",
    responses=ERROR_RESPONSES,
)
async def get_invoice(
    invoice_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    user: CanRead,
) -> ApiResponse[InvoiceDetail]:
    return await controller.get(
        invoice_id=invoice_id,
        user=user,
        can_read_all="invoice.read.all" in user_permissions(user),
    )


@router.get(
    "/{invoice_id}/file",
    response_model=ApiResponse[FileLink],
    summary="Short-lived signed download URL",
    responses=ERROR_RESPONSES,
)
async def invoice_file(
    invoice_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    user: CanRead,
) -> ApiResponse[FileLink]:
    return await controller.file_link(
        invoice_id=invoice_id,
        user=user,
        can_read_all="invoice.read.all" in user_permissions(user),
    )


@router.delete(
    "/{invoice_id}",
    response_model=ApiResponse[None],
    summary="Withdraw your own pending upload, or delete any as admin",
    responses=ERROR_RESPONSES,
)
async def delete_invoice(
    invoice_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    user: CanRead,
) -> ApiResponse[None]:
    return await controller.delete(invoice_id=invoice_id, actor=user)


# ---------------------------------------------------------------------------
# Pipeline
#
# The two long-running steps answer 202, not 200: the work is scheduled, not
# finished. The body carries the status the row moved to so the client knows
# what to poll for instead of guessing.
# ---------------------------------------------------------------------------
@router.post(
    "/{invoice_id}/ocr",
    response_model=ApiResponse[JobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run extraction (requires invoice.read.all)",
    responses=ERROR_RESPONSES,
)
async def start_ocr(
    invoice_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    user: CanReadAll,
    background: BackgroundTasks,
) -> ApiResponse[JobAccepted]:
    return await controller.start_ocr(
        invoice_id=invoice_id, user=user, background=background
    )


@router.post(
    "/{invoice_id}/match",
    response_model=ApiResponse[JobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Match against Odoo purchase orders (requires invoice.approve)",
    responses=ERROR_RESPONSES,
)
async def start_matching(
    invoice_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    user: CanApprove,
    background: BackgroundTasks,
) -> ApiResponse[JobAccepted]:
    return await controller.start_matching(
        invoice_id=invoice_id, user=user, background=background
    )


@router.post(
    "/{invoice_id}/confirm",
    response_model=ApiResponse[InvoiceDetail],
    summary="Accept or override the matched purchase order",
    responses=ERROR_RESPONSES,
)
async def confirm_invoice_match(
    invoice_id: Annotated[uuid.UUID, Path()],
    payload: ConfirmMatchRequest,
    controller: Controller,
    user: CanApprove,
) -> ApiResponse[InvoiceDetail]:
    return await controller.confirm(invoice_id=invoice_id, user=user, payload=payload)


@router.get(
    "/{invoice_id}/po-preview",
    response_model=ApiResponse[PoPreview],
    summary="What creating a purchase order from this invoice would produce",
    responses=ERROR_RESPONSES,
)
async def po_preview(
    invoice_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    user: CanApprove,
) -> ApiResponse[PoPreview]:
    return await controller.po_preview(invoice_id=invoice_id, user=user)


@router.post(
    "/{invoice_id}/create-po",
    response_model=ApiResponse[InvoiceDetail],
    summary="Create a draft purchase order in Odoo from this invoice",
    responses=ERROR_RESPONSES,
)
async def create_purchase_order(
    invoice_id: Annotated[uuid.UUID, Path()],
    payload: CreatePoRequest,
    controller: Controller,
    user: CanApprove,
) -> ApiResponse[InvoiceDetail]:
    """Writes to Odoo. The order is created in draft, so it still needs
    confirming there — and the mapping in the payload is the one a reviewer
    approved, not one resolved on the server."""
    return await controller.create_po(invoice_id=invoice_id, user=user, payload=payload)


@router.post(
    "/{invoice_id}/reject",
    response_model=ApiResponse[InvoiceDetail],
    summary="Reject an invoice with a reason",
    responses=ERROR_RESPONSES,
)
async def reject_invoice_route(
    invoice_id: Annotated[uuid.UUID, Path()],
    payload: RejectInvoiceRequest,
    controller: Controller,
    user: CanApprove,
) -> ApiResponse[InvoiceDetail]:
    return await controller.reject(invoice_id=invoice_id, user=user, payload=payload)
