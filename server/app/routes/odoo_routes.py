"""Odoo passthrough routes.

Read-only and gated on `invoice.approve`, because the purchase order list is
commercial data — who is buying what, at what price — not something a member
uploading their own invoice should be able to enumerate.

Every route resolves the caller's own company and asks THAT company's Odoo.
There is no route here that can reach another company's, which matters more
than the permission does: the permission decides whether somebody may read
purchase orders at all, and the company decides whose.

`/connection` exists for its own sake. Four separate values have to be right
(URL, database, username, key) and diagnosing which one is wrong through a
failing purchase-order fetch is miserable; this answers that in one call, and
it is what a company administrator uses after saving their credentials.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.tenancy import CurrentCompany
from app.lib.responses import ApiErrorResponse, ApiResponse
from app.models.user import User
from app.schemas.odoo import OdooPurchaseOrder
from app.services.odoo_service import odoo_for

router = APIRouter(prefix="/odoo", tags=["odoo"])

CanApprove = Annotated[User, Depends(require_permission("invoice.approve"))]
DbSession = Annotated[AsyncSession, Depends(get_db)]

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
    summary="Verify your company's Odoo credentials (requires invoice.approve)",
    responses=ERROR_RESPONSES,
)
async def check_connection(
    _actor: CanApprove, company: CurrentCompany, db: DbSession
) -> ApiResponse[dict[str, Any]]:
    """Authenticate against this company's Odoo and report what answered.

    The database and URL come back in the response deliberately: an
    administrator who has just saved credentials needs to see WHICH Odoo they
    reached, not merely that something answered.
    """
    odoo = await odoo_for(db, company.id)
    return ApiResponse.ok(
        await odoo.check_connection(), message="Odoo connection OK"
    )


@router.get(
    "/purchase-orders",
    response_model=ApiResponse[list[OdooPurchaseOrder]],
    summary="Open purchase orders awaiting a bill (requires invoice.approve)",
    responses=ERROR_RESPONSES,
)
async def list_purchase_orders(
    _actor: CanApprove,
    company: CurrentCompany,
    db: DbSession,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> ApiResponse[list[OdooPurchaseOrder]]:
    odoo = await odoo_for(db, company.id)
    orders = await odoo.fetch_open_purchase_orders(limit=limit)
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
    company: CurrentCompany,
    db: DbSession,
) -> ApiResponse[OdooPurchaseOrder | None]:
    # The id is an Odoo id, and Odoo ids are only unique within one database —
    # so this reads from the caller's own Odoo and cannot be pointed at
    # another's by guessing a number.
    odoo = await odoo_for(db, company.id)
    order = await odoo.fetch_purchase_order(po_id)
    return ApiResponse.ok(
        order, message="Purchase order retrieved" if order else "Not found in Odoo"
    )
