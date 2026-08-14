"""Odoo passthrough routes.

Read-only and gated on `invoice.approve`, because the purchase order list is
commercial data — who is buying what, at what price — not something a member
uploading their own invoice should be able to enumerate.

`/connection` exists for its own sake. Four separate values have to be right
(URL, database, username, key) and diagnosing which one is wrong through a
failing purchase-order fetch is miserable; this answers that in one call.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from app.dependencies.auth import require_permission
from app.lib.responses import ApiErrorResponse, ApiResponse
from app.models.user import User
from app.schemas.odoo import OdooPurchaseOrder
from app.services.odoo_service import odoo_service

router = APIRouter(prefix="/odoo", tags=["odoo"])

CanApprove = Annotated[User, Depends(require_permission("invoice.approve"))]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    502: {"model": ApiErrorResponse},
    503: {"model": ApiErrorResponse},
}


@router.get(
    "/connection",
    response_model=ApiResponse[dict[str, Any]],
    summary="Verify the Odoo credentials (requires invoice.approve)",
    responses=ERROR_RESPONSES,
)
async def check_connection(_actor: CanApprove) -> ApiResponse[dict[str, Any]]:
    return ApiResponse.ok(
        await odoo_service.check_connection(), message="Odoo connection OK"
    )


@router.get(
    "/purchase-orders",
    response_model=ApiResponse[list[OdooPurchaseOrder]],
    summary="Open purchase orders awaiting a bill (requires invoice.approve)",
    responses=ERROR_RESPONSES,
)
async def list_purchase_orders(
    _actor: CanApprove,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> ApiResponse[list[OdooPurchaseOrder]]:
    orders = await odoo_service.fetch_open_purchase_orders(limit=limit)
    return ApiResponse.ok(
        orders, message=f"{len(orders)} open purchase order(s)"
    )


@router.get(
    "/purchase-orders/{po_id}",
    response_model=ApiResponse[OdooPurchaseOrder | None],
    summary="One purchase order with its lines (requires invoice.approve)",
    responses=ERROR_RESPONSES,
)
async def get_purchase_order(
    po_id: Annotated[int, Path(gt=0)],
    _actor: CanApprove,
) -> ApiResponse[OdooPurchaseOrder | None]:
    order = await odoo_service.fetch_purchase_order(po_id)
    return ApiResponse.ok(
        order, message="Purchase order retrieved" if order else "Not found in Odoo"
    )
