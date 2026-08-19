"""A company's own settings — currently its Odoo connection.

Every route is scoped to the caller's company and gated on `system.admin`, so
one company's administrator can configure their own ERP and nobody else's.
There is no company id in any path or body here; it comes from the session.

The credential is write-only across this whole surface. `OdooConfigStatus`
carries no field that could return it, which makes "the API never leaks the key"
a property of the schema rather than a rule somebody has to remember.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.tenancy import CurrentCompany
from app.lib.responses import ApiErrorResponse, ApiResponse
from app.models.user import User
from app.schemas.company import CompanyRead, OdooConfigStatus, OdooConfigWrite
from app.services.company_service import CompanyService

router = APIRouter(prefix="/company", tags=["company"])

CanAdmin = Annotated[User, Depends(require_permission("system.admin"))]
DbSession = Annotated[AsyncSession, Depends(get_db)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
    502: {"model": ApiErrorResponse},
    503: {"model": ApiErrorResponse},
}


@router.get(
    "",
    response_model=ApiResponse[CompanyRead],
    summary="The company you belong to",
    responses=ERROR_RESPONSES,
)
async def read_company(company: CurrentCompany) -> ApiResponse[CompanyRead]:
    # No permission beyond being signed in: everybody may know which company
    # they are working in, and the header renders it.
    return ApiResponse.ok(
        CompanyRead.model_validate(company), message="Company retrieved"
    )


@router.get(
    "/odoo",
    response_model=ApiResponse[OdooConfigStatus],
    summary="Your company's Odoo connection (requires system.admin)",
    responses=ERROR_RESPONSES,
)
async def odoo_status(actor: CanAdmin, db: DbSession) -> ApiResponse[OdooConfigStatus]:
    return ApiResponse.ok(
        await CompanyService(db).odoo_status(actor=actor),
        message="Odoo configuration retrieved",
    )


@router.put(
    "/odoo",
    response_model=ApiResponse[OdooConfigStatus],
    summary="Save your company's Odoo credentials (requires system.admin)",
    responses=ERROR_RESPONSES,
)
async def save_odoo(
    payload: OdooConfigWrite, actor: CanAdmin, db: DbSession
) -> ApiResponse[OdooConfigStatus]:
    """Store the credentials, encrypted, and drop this company's cached client.

    Saving does not verify. `POST /company/odoo/verify` does that, so a typo is
    reported as a failed connection rather than as a failed save that loses
    what was typed.
    """
    status = await CompanyService(db).save_odoo_config(
        actor=actor,
        base_url=payload.base_url,
        database=payload.database,
        username=payload.username,
        api_key=payload.api_key,
        is_enabled=payload.is_enabled,
    )
    return ApiResponse.ok(status, message="Odoo configuration saved")


@router.post(
    "/odoo/verify",
    response_model=ApiResponse[dict[str, Any]],
    summary="Test your company's Odoo credentials (requires system.admin)",
    responses=ERROR_RESPONSES,
)
async def verify_odoo(actor: CanAdmin, db: DbSession) -> ApiResponse[dict[str, Any]]:
    """Authenticate and report which Odoo answered.

    The database and URL come back deliberately: an administrator who has just
    saved credentials needs to see WHERE they connected, not only that
    something did.
    """
    return ApiResponse.ok(
        await CompanyService(db).verify_odoo(actor=actor),
        message="Odoo connection verified",
    )


@router.post(
    "/odoo/disable",
    response_model=ApiResponse[OdooConfigStatus],
    summary="Switch the Odoo connection off (requires system.admin)",
    responses=ERROR_RESPONSES,
)
async def disable_odoo(
    actor: CanAdmin, db: DbSession
) -> ApiResponse[OdooConfigStatus]:
    """Stop using Odoo without discarding the credentials.

    For an Odoo that is down or mid-migration: matching and billing then fail
    fast with a clear reason instead of timing out against a dead host.
    """
    return ApiResponse.ok(
        await CompanyService(db).disable_odoo(actor=actor),
        message="Odoo connection disabled",
    )
