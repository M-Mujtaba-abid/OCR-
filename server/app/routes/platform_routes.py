"""Platform-owner routes — creating and suspending companies.

Gated on `platform.admin`, which only `SUPER_ADMIN` holds, and which is granted
alongside `user.read.self` and nothing else. The platform owner therefore
cannot reach a single invoice, bill or notification through any route in this
application — not because these routes decline to expose them, but because the
routes that do expose them require permissions this role does not have, and
because scoping any of them needs a company the account does not belong to.

This is also the ONLY router where a company id appears in a path. Everywhere
else it comes from the session; here the caller belongs to no company, and
naming one is the entire job.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.lib.responses import ApiErrorResponse, ApiResponse
from app.models.user import User
from app.schemas.platform import (
    CompanyCreate,
    CompanyCreated,
    PlatformCompany,
    PlatformStats,
)
from app.services.platform_service import PlatformService

router = APIRouter(prefix="/platform", tags=["platform"])

IsPlatformOwner = Annotated[User, Depends(require_permission("platform.admin"))]
DbSession = Annotated[AsyncSession, Depends(get_db)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
}


@router.get(
    "/companies",
    response_model=ApiResponse[list[PlatformCompany]],
    summary="Every company on the platform (requires platform.admin)",
    responses=ERROR_RESPONSES,
)
async def list_companies(
    _actor: IsPlatformOwner, db: DbSession
) -> ApiResponse[list[PlatformCompany]]:
    companies = await PlatformService(db).list_companies()
    return ApiResponse.ok(
        companies, message=f"{len(companies)} company/companies"
    )


@router.get(
    "/stats",
    response_model=ApiResponse[PlatformStats],
    summary="Companies and accounts across the platform (requires platform.admin)",
    responses=ERROR_RESPONSES,
)
async def platform_stats(
    _actor: IsPlatformOwner, db: DbSession
) -> ApiResponse[PlatformStats]:
    # Declared before /companies/{company_id} would matter if it collided; it
    # does not, but the ordering convention is kept for the next route added.
    return ApiResponse.ok(
        await PlatformService(db).stats(), message="Platform stats retrieved"
    )


@router.post(
    "/companies",
    response_model=ApiResponse[CompanyCreated],
    status_code=status.HTTP_201_CREATED,
    summary="Create a company and its first administrator (requires platform.admin)",
    responses=ERROR_RESPONSES,
)
async def create_company(
    payload: CompanyCreate, _actor: IsPlatformOwner, db: DbSession
) -> ApiResponse[CompanyCreated]:
    """Both in one call, because either alone is not a working company.

    The administrator created here is an ADMIN of the new company — never a
    second platform owner. From this point the company runs itself: its admin
    adds its members, configures its Odoo, and the platform owner has no
    further reach into it.
    """
    service = PlatformService(db)
    company, admin = await service.create_company(
        name=payload.name,
        admin_email=payload.admin_email,
        admin_password=payload.admin_password,
        admin_full_name=payload.admin_full_name,
    )
    return ApiResponse.ok(
        CompanyCreated(
            company=PlatformCompany(
                id=company.id,
                name=company.name,
                slug=company.slug,
                is_active=company.is_active,
                created_at=company.created_at,
                user_count=1,
                active_user_count=1,
                admin_count=1,
            ),
            admin_email=admin.email,
            admin_id=admin.id,
        ),
        message=f"{company.name} created",
    )


@router.post(
    "/companies/{company_id}/suspend",
    response_model=ApiResponse[PlatformCompany],
    summary="Suspend a company (requires platform.admin)",
    responses=ERROR_RESPONSES,
)
async def suspend_company(
    company_id: Annotated[uuid.UUID, Path()],
    _actor: IsPlatformOwner,
    db: DbSession,
) -> ApiResponse[PlatformCompany]:
    """Switch every account in the company off at once.

    There is no delete. Invoices and bills are accounting records, and removing
    a company would cascade its audit trail with it — so this is the only
    removal there is, and it is reversible.
    """
    company = await PlatformService(db).set_company_active(
        company_id=company_id, is_active=False
    )
    return ApiResponse.ok(
        PlatformCompany.model_validate(company), message=f"{company.name} suspended"
    )


@router.post(
    "/companies/{company_id}/restore",
    response_model=ApiResponse[PlatformCompany],
    summary="Restore a suspended company (requires platform.admin)",
    responses=ERROR_RESPONSES,
)
async def restore_company(
    company_id: Annotated[uuid.UUID, Path()],
    _actor: IsPlatformOwner,
    db: DbSession,
) -> ApiResponse[PlatformCompany]:
    company = await PlatformService(db).set_company_active(
        company_id=company_id, is_active=True
    )
    return ApiResponse.ok(
        PlatformCompany.model_validate(company), message=f"{company.name} restored"
    )
