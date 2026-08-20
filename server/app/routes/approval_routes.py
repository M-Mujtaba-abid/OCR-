"""Approval chain routes.

Two different kinds of gate live here, and the difference is the design.

Configuring a chain is administration, so it takes a permission —
`approval.configure`. Deciding a rung is not: who may approve step 2 of a given
request is answered by that request's own snapshot, so those routes ask only for
a signed-in account inside the company. That is what lets a business put its
receiving clerk on a step without also handing them everything an admin holds.

Cross-tenant reads answer 404 rather than 403 throughout, matching the rest of
the system: a 403 confirms the id is real, which is the one thing a probe is
looking for.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.approval_controller import ApprovalController
from app.db.session import get_db
from app.dependencies.auth import CurrentActiveUser, require_permission
from app.dependencies.tenancy import CurrentCompany
from app.lib.responses import ApiErrorResponse, ApiResponse
from app.models.user import User
from app.schemas.approval import (
    ApprovalChainRead,
    ApprovalRequestRead,
    AwaitingItem,
    CancelRequest,
    DecideRequest,
    SaveChainRequest,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])


def get_approval_controller(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApprovalController:
    return ApprovalController(db)


Controller = Annotated[ApprovalController, Depends(get_approval_controller)]

#: Writing the policy. Admin-only, and deliberately separate from deciding a
#: rung — an administrator decides WHICH approvals exist, not who gives them.
CanConfigure = Annotated[User, Depends(require_permission("approval.configure"))]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ApiErrorResponse},
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
}


# ---------------------------------------------------------------------------
# Chains — the policy
# ---------------------------------------------------------------------------
@router.get(
    "/chains",
    response_model=ApiResponse[list[ApprovalChainRead]],
    summary="Every approval chain in this company (requires approval.configure)",
    responses=ERROR_RESPONSES,
)
async def list_chains(
    controller: Controller,
    _actor: CanConfigure,
    company: CurrentCompany,
) -> ApiResponse[list[ApprovalChainRead]]:
    return await controller.list_chains(company_id=company.id)


@router.post(
    "/chains",
    response_model=ApiResponse[ApprovalChainRead],
    summary="Create an approval chain (requires approval.configure)",
    responses=ERROR_RESPONSES,
)
async def create_chain(
    payload: SaveChainRequest,
    controller: Controller,
    _actor: CanConfigure,
    company: CurrentCompany,
) -> ApiResponse[ApprovalChainRead]:
    """Validated before anything is written.

    A step with no approver, or one naming somebody who is not an active user of
    this company, is refused here — the moment it is still cheap. The failure
    this prevents is discovering an unsatisfiable rung with a live invoice
    already stuck on it, which nothing in the product can then free.
    """
    return await controller.save_chain(
        company_id=company.id, chain_id=None, payload=payload
    )


@router.put(
    "/chains/{chain_id}",
    response_model=ApiResponse[ApprovalChainRead],
    summary="Replace an approval chain (requires approval.configure)",
    responses=ERROR_RESPONSES,
)
async def update_chain(
    chain_id: Annotated[uuid.UUID, Path()],
    payload: SaveChainRequest,
    controller: Controller,
    _actor: CanConfigure,
    company: CurrentCompany,
) -> ApiResponse[ApprovalChainRead]:
    """Steps are replaced wholesale, and requests already running are unaffected
    — each carries its own copy of the chain it started with."""
    return await controller.save_chain(
        company_id=company.id, chain_id=chain_id, payload=payload
    )


@router.delete(
    "/chains/{chain_id}",
    response_model=ApiResponse[None],
    summary="Delete an unused approval chain (requires approval.configure)",
    responses=ERROR_RESPONSES,
)
async def delete_chain(
    chain_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    _actor: CanConfigure,
    company: CurrentCompany,
) -> ApiResponse[None]:
    """Only a chain that is neither active nor used.

    An active chain refuses, because deleting it would stop gating every bill in
    the company as a side effect of a button that says something else. A chain
    any request has run through refuses too: those rows are the record of who
    authorised a payment. Both answer 409 with the reason.
    """
    return await controller.delete_chain(
        company_id=company.id, chain_id=chain_id
    )


@router.post(
    "/chains/{chain_id}/activate",
    response_model=ApiResponse[ApprovalChainRead],
    summary="Make this chain gate vendor bills (requires approval.configure)",
    responses=ERROR_RESPONSES,
)
async def activate_chain(
    chain_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    _actor: CanConfigure,
    company: CurrentCompany,
) -> ApiResponse[ApprovalChainRead]:
    """The switch that turns the feature on for a company.

    From here every vendor bill needs its chain completed first. Re-validated at
    this moment rather than trusting the last save: an approver can be
    deactivated in between, and this is the last point before the chain starts
    stopping bills.
    """
    return await controller.set_active(
        company_id=company.id, chain_id=chain_id, active=True
    )


@router.post(
    "/chains/{chain_id}/deactivate",
    response_model=ApiResponse[ApprovalChainRead],
    summary="Stop this chain gating vendor bills (requires approval.configure)",
    responses=ERROR_RESPONSES,
)
async def deactivate_chain(
    chain_id: Annotated[uuid.UUID, Path()],
    controller: Controller,
    _actor: CanConfigure,
    company: CurrentCompany,
) -> ApiResponse[ApprovalChainRead]:
    """Billing returns to what it was before the chain existed. Requests already
    running are left alone — they are the record of decisions people made."""
    return await controller.set_active(
        company_id=company.id, chain_id=chain_id, active=False
    )


# ---------------------------------------------------------------------------
# Requests — the record
# ---------------------------------------------------------------------------
@router.get(
    "/awaiting-me",
    response_model=ApiResponse[list[AwaitingItem]],
    summary="Approval requests waiting on you",
    responses=ERROR_RESPONSES,
)
async def awaiting_me(
    controller: Controller,
    user: CurrentActiveUser,
    company: CurrentCompany,
) -> ApiResponse[list[AwaitingItem]]:
    """No permission gate, on purpose.

    Eligibility comes from each request's own step snapshot, so this answers
    "what is waiting on YOU" and cannot answer anything else. A permission here
    would only decide who gets an empty list.
    """
    return await controller.awaiting(company_id=company.id, user=user)


@router.post(
    "/{request_id}/decide",
    response_model=ApiResponse[ApprovalRequestRead],
    summary="Approve or decline the step you are on",
    responses=ERROR_RESPONSES,
)
async def decide(
    request_id: Annotated[uuid.UUID, Path()],
    payload: DecideRequest,
    controller: Controller,
    user: CurrentActiveUser,
    company: CurrentCompany,
) -> ApiResponse[ApprovalRequestRead]:
    """Whether you may decide is answered by the request, not by your role.

    Three rules, all read from its frozen snapshot: you are named on the current
    rung, you are not the person who asked (unless the chain allowed that), and
    you have not already decided an earlier rung — because one person approving
    two rungs turns a three-step chain into a two-person one.

    A decline needs a reason and sends the invoice back to the status it held
    before the chain, not to the review queue.
    """
    return await controller.decide(
        request_id=request_id, company_id=company.id, user=user, payload=payload
    )


@router.post(
    "/{request_id}/cancel",
    response_model=ApiResponse[ApprovalRequestRead],
    summary="Pull an invoice out of its chain (requires approval.configure)",
    responses=ERROR_RESPONSES,
)
async def cancel(
    request_id: Annotated[uuid.UUID, Path()],
    payload: CancelRequest,
    controller: Controller,
    user: CanConfigure,
    company: CurrentCompany,
) -> ApiResponse[ApprovalRequestRead]:
    """The escape hatch, and deliberately an auditable one.

    Every approver on a rung being deactivated at once is rare but possible, and
    the alternative to this endpoint is somebody editing the database by hand —
    which leaves no record that a payment bypassed its chain. Here it leaves a
    cancelled decision with a reason on it.
    """
    return await controller.cancel(
        request_id=request_id, company_id=company.id, user=user, payload=payload
    )
