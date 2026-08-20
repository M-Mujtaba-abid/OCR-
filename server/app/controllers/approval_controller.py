"""Approval controller: HTTP in, HTTP out."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.lib.responses import ApiResponse
from app.models.approval import ApprovalRequest
from app.models.user import User
from app.schemas.approval import (
    ApprovalChainRead,
    ApprovalDecisionRead,
    ApprovalLineRead,
    ApprovalRequestRead,
    ApprovalStepProgress,
    AwaitingItem,
    CancelRequest,
    DecideRequest,
    InvoiceApprovalRead,
    RequestApprovalRequest,
    SaveChainRequest,
)
from app.services.approval_service import ApprovalService
from app.services.bill_creator_service import quote_for_approval
from app.services.invoice_service import InvoiceService


def _to_read(request: ApprovalRequest) -> ApprovalRequestRead:
    """Serialise a request, merging its decisions onto its steps.

    Built field by field rather than by `model_validate(request)` on the ORM
    object: `steps` here is the snapshot with outcomes folded in, which is not a
    relationship and has no attribute to read. Doing it explicitly also keeps
    the two lazy="raise" relationships honest — anything this touches has to
    have been eagerly loaded, and a missing option fails here rather than deep
    inside serialisation.
    """
    by_position = {
        decision.position: ApprovalDecisionRead.model_validate(decision)
        for decision in request.decisions
    }
    steps = [
        ApprovalStepProgress(
            position=int(step["position"]),
            name=str(step["name"]),
            approver_user_ids=[uuid.UUID(str(u)) for u in step["approver_user_ids"]],
            records_receipt=bool(step.get("records_receipt", False)),
            decision=by_position.get(int(step["position"])),
            is_current=int(step["position"]) == request.current_position,
        )
        for step in request.steps_snapshot
    ]
    return ApprovalRequestRead(
        id=request.id,
        invoice_id=request.invoice_id,
        status=request.status,
        current_position=request.current_position,
        amount_at_request=request.amount_at_request,
        status_before_approval=request.status_before_approval,
        allow_self_approval=request.allow_self_approval,
        requested_by=request.requested_by,
        requester=request.requester,  # type: ignore[arg-type]
        created_at=request.created_at,
        current_step_since=request.current_step_since,
        waiting_days=max(
            0,
            (dt.datetime.now(dt.UTC) - request.current_step_since).days,
        ),
        steps=steps,
        lines=[ApprovalLineRead(**line) for line in request.lines_snapshot],
        po_id=request.po_id,
        receipt=request.receipt,
    )


class ApprovalController:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.service = ApprovalService(db)
        self.invoices = InvoiceService(db)

    async def _reload(
        self, request_id: uuid.UUID, company_id: uuid.UUID
    ) -> ApprovalRequestRead:
        """Re-read a request after writing to it, with its relationships loaded.

        The service returns the object it mutated, whose `decisions` collection
        is lazy="raise" and — on a request created moments ago — was never
        loaded. Serialising that raises, so every write path reloads rather than
        serialising what it was handed.
        """
        request = await self.service.repo.find_request(
            request_id, company_id=company_id
        )
        if request is None:  # pragma: no cover — it was written a moment ago
            raise NotFoundError("Approval request not found.")
        return _to_read(request)

    # ------------------------------------------------------------------ chains
    async def list_chains(
        self, *, company_id: uuid.UUID
    ) -> ApiResponse[list[ApprovalChainRead]]:
        chains = await self.service.list_chains(company_id=company_id)
        return ApiResponse.ok(
            [ApprovalChainRead.model_validate(chain) for chain in chains]
        )

    async def save_chain(
        self,
        *,
        company_id: uuid.UUID,
        chain_id: uuid.UUID | None,
        payload: SaveChainRequest,
    ) -> ApiResponse[ApprovalChainRead]:
        chain = await self.service.save_chain(
            company_id=company_id,
            chain_id=chain_id,
            name=payload.name,
            allow_self_approval=payload.allow_self_approval,
            steps=[
                {
                    "name": step.name,
                    "approver_user_ids": [str(u) for u in step.approver_user_ids],
                    "records_receipt": step.records_receipt,
                }
                for step in payload.steps
            ],
            is_active=payload.is_active,
        )
        await self.db.commit()
        return ApiResponse.ok(
            ApprovalChainRead.model_validate(chain),
            message=f"Saved {chain.name}"
            + (" and made it active" if chain.is_active else ""),
        )

    async def set_active(
        self, *, company_id: uuid.UUID, chain_id: uuid.UUID, active: bool
    ) -> ApiResponse[ApprovalChainRead]:
        chain = await self.service.set_active(
            company_id=company_id, chain_id=chain_id, active=active
        )
        await self.db.commit()
        # Reloaded so the response carries the steps: `set_active` returns the
        # row it updated, and its `steps` collection is lazy="raise".
        fresh = await self.service.repo.find_chain(chain.id, company_id=company_id)
        if fresh is None:  # pragma: no cover — written a moment ago
            raise NotFoundError("Approval chain not found.")
        return ApiResponse.ok(
            ApprovalChainRead.model_validate(fresh),
            message=(
                f"{chain.name} is now gating vendor bills"
                if active
                else f"{chain.name} is no longer gating vendor bills"
            ),
        )

    async def delete_chain(
        self, *, company_id: uuid.UUID, chain_id: uuid.UUID
    ) -> ApiResponse[None]:
        name = await self.service.delete_chain(
            company_id=company_id, chain_id=chain_id
        )
        await self.db.commit()
        return ApiResponse.ok(None, message=f"Deleted {name}")

    # ---------------------------------------------------------------- requests
    async def request_approval(
        self, *, invoice_id: uuid.UUID, user: User, payload: RequestApprovalRequest
    ) -> ApiResponse[ApprovalRequestRead]:
        invoice = await self.invoices.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True, with_lines=True
        )
        # Priced against Odoo before anything is written, so the chain fails on
        # the same problems billing would — while it is still one person's click
        # rather than three signatures later.
        lines = await quote_for_approval(
            self.db,
            invoice=invoice,
            po_id=payload.po_id,
            lines=[line.model_dump() for line in payload.lines],
        )
        request = await self.service.request_approval(
            invoice=invoice,
            requested_by=user.id,
            po_id=payload.po_id,
            lines=lines,
        )
        await self.db.commit()
        return ApiResponse.ok(
            await self._reload(request.id, invoice.company_id),
            message="Sent for approval",
        )

    async def for_invoice(
        self, *, invoice_id: uuid.UUID, user: User
    ) -> ApiResponse[InvoiceApprovalRead]:
        # Through get_for_user so an invoice in another company answers 404 here
        # exactly as it does everywhere else — never 403, which would confirm
        # the id is real.
        invoice = await self.invoices.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=True
        )
        request = await self.service.for_invoice(
            invoice_id=invoice.id, company_id=invoice.company_id
        )
        # Fetched here rather than left to the client: reading the chain list
        # needs `approval.configure`, and the caller who most needs to know a
        # chain exists is a manager, who does not hold it.
        chain = await self.service.get_active_chain(company_id=invoice.company_id)
        return ApiResponse.ok(
            InvoiceApprovalRead(
                chain_active=chain is not None,
                chain_name=chain.name if chain else None,
                request=None if request is None else _to_read(request),
            )
        )

    async def awaiting(
        self, *, company_id: uuid.UUID, user: User
    ) -> ApiResponse[list[AwaitingItem]]:
        requests = await self.service.awaiting(
            company_id=company_id, user_id=user.id
        )
        summaries = await self.service.repo.invoice_summaries(
            [request.invoice_id for request in requests], company_id=company_id
        )

        items: list[AwaitingItem] = []
        for request in requests:
            summary = summaries.get(request.invoice_id)
            if summary is None:
                # The invoice went away underneath a pending request. Skipping
                # beats rendering a row that opens onto a 404.
                continue
            read = _to_read(request)
            current = next(
                (step for step in read.steps if step.is_current), None
            )
            items.append(
                AwaitingItem(
                    request=read,
                    invoice_id=request.invoice_id,
                    file_name=summary["file_name"],
                    vendor=summary["vendor"],
                    invoice_no=summary["invoice_no"],
                    step_name=current.name if current else "",
                    step_position=request.current_position,
                )
            )
        return ApiResponse.ok(items)

    async def decide(
        self,
        *,
        request_id: uuid.UUID,
        company_id: uuid.UUID,
        user: User,
        payload: DecideRequest,
    ) -> ApiResponse[ApprovalRequestRead]:
        request = await self.service.decide(
            request_id=request_id,
            company_id=company_id,
            user=user,
            approve=payload.approve,
            reason=payload.reason,
        )
        await self.db.commit()
        return ApiResponse.ok(
            await self._reload(request.id, company_id),
            message="Approved" if payload.approve else "Sent back with your reason",
        )

    async def cancel(
        self,
        *,
        request_id: uuid.UUID,
        company_id: uuid.UUID,
        user: User,
        payload: CancelRequest,
    ) -> ApiResponse[ApprovalRequestRead]:
        request = await self.service.cancel(
            request_id=request_id,
            company_id=company_id,
            user=user,
            reason=payload.reason,
        )
        await self.db.commit()
        return ApiResponse.ok(
            await self._reload(request.id, company_id), message="Cancelled"
        )
