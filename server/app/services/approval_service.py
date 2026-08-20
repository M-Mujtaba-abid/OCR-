"""Approval chains: defining them, running them, and gating the bill.

Three jobs, in the order they matter.

**Defining.** A chain is validated when it is SAVED, not when an invoice hits it.
The failure this exists to prevent is a step nobody can satisfy — a named
approver who left the company — discovered with a live invoice already stuck on
it, at which point the only way out is a hand-written UPDATE.

**Running.** A request freezes the chain it started with. Everything downstream
reads `steps_snapshot`, never `approval_steps`, so an admin reorganising the
policy cannot change who still has to approve something already in motion.

**Gating.** `gate_for_billing` is what makes any of this real. An advisory chain
is one that gets skipped on the first busy afternoon, so the check lives in the
billing path itself rather than in a screen that can be bypassed.

Like every other service here, nothing in this module commits. The caller owns
the transaction, so an approval and the notification announcing it either both
land or neither does.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ApprovalRequiredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.lib.logging import get_logger
from app.models.approval import (
    ApprovalChain,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestStatus,
)
from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.notification import NotificationType
from app.models.user import User
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.match_history_repository import MatchHistoryRepository
from app.repositories.user_repository import UserRepository
from app.services.notification_service import NotificationService

logger = get_logger(__name__)

#: A chain longer than this is a process problem, not a configuration one. The
#: cap exists so a stray loop in a client cannot write a thousand-rung chain
#: that no invoice can ever finish.
MAX_STEPS = 12


# ---------------------------------------------------------------------------
# Pure helpers
#
# No I/O. The rules a chain has to obey are testable against literals, which is
# the same reason `check_over_billing` is shaped this way.
# ---------------------------------------------------------------------------
def step_at(request: ApprovalRequest, position: int) -> dict[str, Any] | None:
    """The snapshot rung at `position`, or None if the chain has no such rung.

    Indexed by the stored `position` field rather than by list offset. They
    agree today — the repository writes them contiguously from 1 — but a lookup
    that quietly returns the wrong rung is a worse failure than one that returns
    nothing.
    """
    for step in request.steps_snapshot:
        if int(step.get("position", 0)) == position:
            return step
    return None


def approvers_of(step: dict[str, Any]) -> set[uuid.UUID]:
    """The rung's eligible deciders, as UUIDs.

    JSONB gives them back as strings; every comparison in this module is against
    a real `uuid.UUID`, and `"abc" == UUID("abc")` is quietly False.
    """
    out: set[uuid.UUID] = set()
    for raw in step.get("approver_user_ids", []):
        try:
            out.add(uuid.UUID(str(raw)))
        except ValueError:  # pragma: no cover — only a hand-edited row gets here
            logger.warning("approval.snapshot_bad_uuid", extra={"value": str(raw)})
    return out


def may_decide(
    request: ApprovalRequest, *, user_id: uuid.UUID, position: int
) -> bool:
    """Whether this person may decide this rung of this request.

    Three rules, all read from the request's own frozen copy:

    1. They are named on the rung.
    2. They are not the person who asked — unless the chain allowed that when
       the request began.
    3. They have not already decided an earlier rung. One person approving two
       rungs turns a three-step chain into a two-person one without anybody
       editing it, which is the whole control quietly evaporating.
    """
    step = step_at(request, position)
    if step is None:
        return False
    if user_id not in approvers_of(step):
        return False
    if not request.allow_self_approval and request.requested_by == user_id:
        return False
    return not any(
        decision.decided_by == user_id for decision in request.decisions
    )


def is_final_step(request: ApprovalRequest, position: int) -> bool:
    positions = [int(step.get("position", 0)) for step in request.steps_snapshot]
    return bool(positions) and position >= max(positions)


def amount_of(lines: list[dict[str, Any]]) -> Decimal:
    """What the snapshotted lines come to, tax included.

    Computed from the prices frozen into the snapshot rather than re-read from
    Odoo, so every approver on a chain sees the same figure as the first one and
    the record says what was actually agreed.
    """
    total = Decimal("0")
    for line in lines:
        quantity = Decimal(str(line.get("quantity", 0)))
        unit_price = Decimal(str(line.get("unit_price", 0)))
        tax_rate = Decimal(str(line.get("tax_rate", 0)))
        total += quantity * unit_price * (Decimal("1") + tax_rate)
    return total.quantize(Decimal("0.0001"))


class ApprovalService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = ApprovalRepository(db)
        self.users = UserRepository(db)
        self.invoices = MatchHistoryRepository(db)
        self.notifications = NotificationService(db)

    # ------------------------------------------------------------------ chains
    async def list_chains(self, *, company_id: uuid.UUID) -> list[ApprovalChain]:
        return await self.repo.list_chains(company_id=company_id)

    async def get_active_chain(
        self, *, company_id: uuid.UUID
    ) -> ApprovalChain | None:
        return await self.repo.active_chain(company_id=company_id)

    async def _validate_steps(
        self, steps: list[dict[str, Any]], *, company_id: uuid.UUID
    ) -> None:
        """Refuse a chain that cannot be satisfied, while it is still cheap.

        This is the check the whole "configurable" idea rests on. Every other
        failure mode here is recoverable by editing a chain; a rung whose only
        approver has left the company strands every invoice that reaches it, and
        nothing in the product can free them.
        """
        if not steps:
            raise ValidationError("An approval chain needs at least one step.")
        if len(steps) > MAX_STEPS:
            raise ValidationError(
                f"An approval chain may have at most {MAX_STEPS} steps."
            )

        named: set[uuid.UUID] = set()
        for index, step in enumerate(steps, start=1):
            if not str(step.get("name", "")).strip():
                raise ValidationError(f"Step {index} needs a name.")
            if not step.get("approver_user_ids"):
                raise ValidationError(
                    f"Step {index} has nobody who can approve it. A step with no "
                    f"approver stops every invoice that reaches it."
                )
            named.update(uuid.UUID(str(raw)) for raw in step["approver_user_ids"])

        # One query for every approver across every step, rather than one per
        # step: a seven-rung chain should cost one round trip to validate.
        active = await self.users.filter_active_ids(
            sorted(named), company_id=company_id
        )
        missing = named - active
        if missing:
            raise ValidationError(
                "These approvers are not active users of this company: "
                + ", ".join(sorted(str(user_id) for user_id in missing)),
                details={"missing_user_ids": sorted(str(u) for u in missing)},
            )

    async def save_chain(
        self,
        *,
        company_id: uuid.UUID,
        chain_id: uuid.UUID | None,
        name: str,
        allow_self_approval: bool,
        steps: list[dict[str, Any]],
        is_active: bool,
    ) -> ApprovalChain:
        """Create or replace a chain, validated before anything is written.

        Validation runs even when the chain is being saved inactive. A chain
        saved broken is one an admin will activate later and discover then, and
        "later" is exactly when it is expensive.
        """
        await self._validate_steps(steps, company_id=company_id)

        if chain_id is None:
            chain = await self.repo.create_chain(
                company_id=company_id,
                name=name.strip(),
                allow_self_approval=allow_self_approval,
                is_active=False,
            )
        else:
            chain = await self.repo.find_chain(chain_id, company_id=company_id)
            if chain is None:
                # 404 rather than 403 for a chain in another company: a 403
                # confirms the id is real, which is the one thing a cross-tenant
                # probe is looking for.
                raise NotFoundError("Approval chain not found.")
            await self.repo.update_chain(
                chain, name=name.strip(), allow_self_approval=allow_self_approval
            )

        await self.repo.replace_steps(chain, steps)
        if is_active != chain.is_active:
            await self.set_active(
                company_id=company_id, chain_id=chain.id, active=is_active
            )

        # Re-read: `replace_steps` deleted and re-inserted underneath the loaded
        # collection, so the object in hand describes steps that no longer exist.
        saved = await self.repo.find_chain(chain.id, company_id=company_id)
        if saved is None:  # pragma: no cover — written a moment ago
            raise NotFoundError("Approval chain not found.")
        return saved

    async def set_active(
        self, *, company_id: uuid.UUID, chain_id: uuid.UUID, active: bool
    ) -> ApprovalChain:
        chain = await self.repo.find_chain(chain_id, company_id=company_id)
        if chain is None:
            raise NotFoundError("Approval chain not found.")

        if active:
            steps = [
                {
                    "name": step.name,
                    "approver_user_ids": [str(u) for u in step.approver_user_ids],
                }
                for step in chain.steps
            ]
            # Re-validated at the moment of activation, not just at save. An
            # approver can be deactivated between the two, and this is the last
            # point before the chain starts stopping bills.
            await self._validate_steps(steps, company_id=company_id)

            current = await self.repo.active_chain(company_id=company_id)
            if current is not None and current.id != chain.id:
                # Deactivate first and flush, or the partial unique index sees
                # two active rows for one company inside the same statement.
                await self.repo.update_chain(current, is_active=False)

        await self.repo.update_chain(chain, is_active=active)
        logger.info(
            "approval.chain_activated" if active else "approval.chain_deactivated",
            extra={"chain_id": str(chain.id), "company_id": str(company_id)},
        )
        return chain

    async def delete_chain(self, *, company_id: uuid.UUID, chain_id: uuid.UUID) -> str:
        """Remove a chain that was never used. Returns its name, for the message.

        Two refusals, and both are the point of having a delete at all rather
        than a hidden one.

        An ACTIVE chain is not deletable, because deleting it would silently
        stop gating every bill in the company — the same effect as switching
        approvals off, reached by a button that says something else. Stand it
        down first, deliberately.

        A chain any request has run through is not deletable either. Those rows
        are the record of who authorised a payment, and while each carries its
        own snapshot of the steps, the chain is what the request points AT. The
        RESTRICT foreign key enforces this regardless; checking here is what
        turns a constraint violation into a sentence.
        """
        chain = await self.repo.find_chain(chain_id, company_id=company_id)
        if chain is None:
            raise NotFoundError("Approval chain not found.")
        if chain.is_active:
            raise ConflictError(
                f"{chain.name} is gating vendor bills. Stop it gating them "
                f"first, so switching approvals off is a decision rather than a "
                f"side effect.",
                code="CHAIN_ACTIVE",
            )
        if await self.repo.chain_in_use(chain_id, company_id=company_id):
            raise ConflictError(
                f"{chain.name} has approvals recorded against it, and those are "
                f"the record of who authorised a payment. It can be left unused "
                f"but not removed.",
                code="CHAIN_IN_USE",
            )

        name = chain.name
        await self.repo.delete_chain(chain)
        logger.info(
            "approval.chain_deleted",
            extra={"chain_id": str(chain_id), "company_id": str(company_id)},
        )
        return name

    # ---------------------------------------------------------------- requests
    async def request_approval(
        self,
        *,
        invoice: MatchHistory,
        requested_by: uuid.UUID,
        lines: list[dict[str, Any]],
    ) -> ApprovalRequest:
        """Start a chain for this invoice.

        `lines` are the priced, Odoo-validated lines from
        `bill_creator_service.quote_for_approval` — quantities, unit prices and
        tax rates together. They are frozen here and are what the approvers
        actually see, which is the point: approving an invoice is worth little
        unless it is also approving an amount.
        """
        company_id = invoice.company_id

        chain = await self.repo.active_chain(company_id=company_id)
        if chain is None:
            raise ConflictError(
                "This company has no active approval chain.",
                code="NO_ACTIVE_CHAIN",
            )

        if invoice.pushed_to_odoo or invoice.odoo_bill_id:
            raise ConflictError(
                "A vendor bill already exists for this invoice.",
                code="BILL_ALREADY_CREATED",
            )

        existing = await self.repo.open_request(invoice.id, company_id=company_id)
        if existing is not None:
            raise ConflictError(
                "This invoice is already waiting for approval.",
                code="APPROVAL_ALREADY_PENDING",
            )

        snapshot = [
            {
                "position": step.position,
                "name": step.name,
                "approver_user_ids": [str(u) for u in step.approver_user_ids],
            }
            for step in chain.steps
        ]
        await self._validate_steps(
            [
                {"name": s["name"], "approver_user_ids": s["approver_user_ids"]}
                for s in snapshot
            ],
            company_id=company_id,
        )

        # The unsatisfiable-chain check that can only be made here, because only
        # here is the requester known: a rung whose every approver is the person
        # asking can never be decided, and refusing now beats stranding the
        # invoice on it.
        if not chain.allow_self_approval:
            for step in snapshot:
                if approvers_of(step) == {requested_by}:
                    raise ConflictError(
                        f"Step {step['position']} ({step['name']}) can only be "
                        f"approved by you, and you are the one asking. Add "
                        f"another approver to that step, or allow "
                        f"self-approval on the chain.",
                        code="CHAIN_UNSATISFIABLE",
                    )

        request = await self.repo.create_request(
            company_id=company_id,
            invoice_id=invoice.id,
            chain_id=chain.id,
            status=ApprovalRequestStatus.PENDING,
            requested_by=requested_by,
            current_position=1,
            amount_at_request=self._amount_at_request(invoice, lines),
            status_before_approval=invoice.status,
            allow_self_approval=chain.allow_self_approval,
            steps_snapshot=snapshot,
            lines_snapshot=lines,
        )

        await self.invoices.update(invoice, status=InvoiceStatus.PENDING_APPROVAL)
        await self._notify_step(
            request, position=1, invoice=invoice, already_decided=set()
        )

        logger.info(
            "approval.requested",
            extra={
                "request_id": str(request.id),
                "invoice_id": str(invoice.id),
                "company_id": str(company_id),
                "steps": len(snapshot),
            },
        )
        return request

    @staticmethod
    def _amount_at_request(
        invoice: MatchHistory, lines: list[dict[str, Any]]
    ) -> Decimal:
        """`max(what the document says, what Odoo's prices say)`.

        The larger of the two on purpose. The invoice total is read off the page
        by OCR and can be under-read; the priced lines come from the order's own
        unit prices and cannot. Taking the maximum means neither a misread
        document nor a thin set of proposed lines can make the approved figure
        look smaller than what was really on the table.
        """
        from_lines = amount_of(lines)
        if invoice.extracted_total is None:
            return from_lines
        return max(from_lines, Decimal(str(invoice.extracted_total)))

    async def decide(
        self,
        *,
        request_id: uuid.UUID,
        company_id: uuid.UUID,
        user: User,
        approve: bool,
        reason: str | None,
    ) -> ApprovalRequest:
        request = await self.repo.find_request(request_id, company_id=company_id)
        if request is None:
            raise NotFoundError("Approval request not found.")
        if request.status is not ApprovalRequestStatus.PENDING:
            raise ConflictError(
                f"This request is already {request.status.value}.",
                code="APPROVAL_CLOSED",
            )

        position = request.current_position
        if not may_decide(request, user_id=user.id, position=position):
            raise ForbiddenError("It is not your turn to decide this request.")

        # Captured before the new decision is written, and used to keep the next
        # rung's notification off anybody who has already had their say.
        already_decided = {
            decision.decided_by
            for decision in request.decisions
            if decision.decided_by is not None
        } | {user.id}

        if not approve and not (reason or "").strip():
            # A decline without a reason sends the invoice back to somebody who
            # cannot act on it.
            raise ValidationError("Say why you are declining this request.")

        invoice = await self.invoices.find_by_id(request.invoice_id)
        if invoice is None or invoice.company_id != company_id:
            raise NotFoundError("Approval request not found.")

        await self._record(
            request,
            position=position,
            user_id=user.id,
            decision=ApprovalDecision.APPROVED
            if approve
            else ApprovalDecision.DECLINED,
            reason=reason,
        )

        if not approve:
            return await self._close(
                request,
                invoice,
                status=ApprovalRequestStatus.DECLINED,
                type=NotificationType.APPROVAL_DECLINED,
                title=f"Approval declined: {invoice.file_name}",
                message=reason,
            )

        if is_final_step(request, position):
            return await self._close(
                request,
                invoice,
                status=ApprovalRequestStatus.APPROVED,
                type=NotificationType.APPROVAL_GRANTED,
                title=f"Approved for billing: {invoice.file_name}",
                message=f"{user.full_name or user.email} gave the final approval.",
            )

        if not await self.repo.advance(request, expect=position):
            # The conditional UPDATE found the row already moved. The decision
            # insert above should have caught this first, so reaching here means
            # something advanced the request outside this path.
            raise ConflictError(
                "Somebody else decided this step first.", code="APPROVAL_RACED"
            )
        await self._notify_step(
            request,
            position=position + 1,
            invoice=invoice,
            already_decided=already_decided,
        )
        return request

    async def cancel(
        self,
        *,
        request_id: uuid.UUID,
        company_id: uuid.UUID,
        user: User,
        reason: str,
    ) -> ApprovalRequest:
        """Pull an invoice out of a chain nobody can satisfy.

        The escape hatch, and deliberately an auditable one. Every approver on a
        rung being deactivated at once is rare but not impossible, and the
        alternative to this endpoint is somebody editing the database by hand —
        which leaves no record that a payment bypassed its chain.
        """
        request = await self.repo.find_request(request_id, company_id=company_id)
        if request is None:
            raise NotFoundError("Approval request not found.")
        if request.status is not ApprovalRequestStatus.PENDING:
            raise ConflictError(
                f"This request is already {request.status.value}.",
                code="APPROVAL_CLOSED",
            )
        if not reason.strip():
            raise ValidationError("Say why you are cancelling this request.")

        invoice = await self.invoices.find_by_id(request.invoice_id)
        if invoice is None or invoice.company_id != company_id:
            raise NotFoundError("Approval request not found.")

        await self._record(
            request,
            position=request.current_position,
            user_id=user.id,
            decision=ApprovalDecision.CANCELLED,
            reason=reason,
        )
        logger.warning(
            "approval.cancelled",
            extra={
                "request_id": str(request.id),
                "invoice_id": str(invoice.id),
                "company_id": str(company_id),
                "by": str(user.id),
            },
        )
        return await self._close(
            request,
            invoice,
            status=ApprovalRequestStatus.CANCELLED,
            type=NotificationType.APPROVAL_DECLINED,
            title=f"Approval cancelled: {invoice.file_name}",
            message=reason,
        )

    async def abandon_for_invoice(
        self, invoice: MatchHistory, *, by: uuid.UUID, reason: str
    ) -> ApprovalRequest | None:
        """Close any running chain because the invoice itself is gone.

        Called when an invoice is rejected. Without it the request stays pending
        forever: it would sit in its approvers' queues asking them to sign off
        something already thrown away, and nothing in the product would clear
        it — the invoice can no longer be billed, so nobody would ever go and
        decide the rung that unblocks it.

        Deliberately does NOT restore `status_before_approval`. The caller is
        setting the invoice to REJECTED, and putting it back where it came from
        is the opposite of what rejecting means.
        """
        request = await self.repo.open_request(
            invoice.id, company_id=invoice.company_id
        )
        if request is None:
            return None

        await self._record(
            request,
            position=request.current_position,
            user_id=by,
            decision=ApprovalDecision.CANCELLED,
            reason=f"The invoice was rejected: {reason}"[:2000],
        )
        await self.repo.update_request(
            request, status=ApprovalRequestStatus.CANCELLED
        )
        logger.info(
            "approval.abandoned_with_invoice",
            extra={
                "request_id": str(request.id),
                "invoice_id": str(invoice.id),
                "company_id": str(invoice.company_id),
            },
        )
        return request

    async def awaiting(
        self, *, company_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[ApprovalRequest]:
        """Every request currently waiting on this person.

        Filtered in Python against each request's own snapshot rather than in
        SQL. Which rung a request is on and who may decide it live in the same
        JSONB document, and indexing into it by a value from another column of
        the same row is a query that reads far worse than this loop — over a set
        bounded by one company's in-flight invoices.
        """
        pending = await self.repo.list_pending(company_id=company_id)
        return [
            request
            for request in pending
            if may_decide(
                request, user_id=user_id, position=request.current_position
            )
        ]

    async def for_invoice(
        self, *, invoice_id: uuid.UUID, company_id: uuid.UUID
    ) -> ApprovalRequest | None:
        """The latest request for an invoice, whatever became of it.

        A declined request is still the honest answer to "where did this get to"
        until somebody submits another.
        """
        return await self.repo.latest_request(invoice_id, company_id=company_id)

    # -------------------------------------------------------------- the gate
    async def gate_for_billing(self, invoice: MatchHistory) -> ApprovalRequest | None:
        """Refuse the bill unless the chain says it is time. Returns the
        approved request, or None when the company gates nothing.

        Called from inside `create_bill_for_invoice` rather than from a route
        guard, and that placement is the feature. A permission says who MAY bill;
        this says whether it is time. An advisory chain — one enforced by hiding
        a button — is a chain that gets bypassed the first busy afternoon.
        """
        chain = await self.repo.active_chain(company_id=invoice.company_id)
        if chain is None:
            # Unchanged behaviour for every company that has not switched this
            # on. Logged rather than silent, because "why did that bill go
            # through unapproved" should be answerable from the logs.
            logger.info(
                "approval.ungated",
                extra={
                    "invoice_id": str(invoice.id),
                    "company_id": str(invoice.company_id),
                },
            )
            return None

        open_request = await self.repo.open_request(
            invoice.id, company_id=invoice.company_id
        )
        if open_request is not None:
            step = step_at(open_request, open_request.current_position)
            raise ApprovalRequiredError(
                f"This invoice is waiting for approval at step "
                f"{open_request.current_position}"
                + (f" ({step['name']})." if step else "."),
                code="APPROVAL_PENDING",
            )

        latest = await self.repo.latest_request(
            invoice.id, company_id=invoice.company_id
        )
        if latest is None or latest.status is not ApprovalRequestStatus.APPROVED:
            raise ApprovalRequiredError(
                "This invoice has to be approved before it can be billed.",
                code="APPROVAL_REQUIRED",
            )
        return latest

    # ------------------------------------------------------------- internals
    async def _record(
        self,
        request: ApprovalRequest,
        *,
        position: int,
        user_id: uuid.UUID,
        decision: ApprovalDecision,
        reason: str | None,
    ) -> None:
        """Insert the decision, letting the unique constraint arbitrate.

        Inside a SAVEPOINT because a failed flush poisons the surrounding
        transaction, and the surrounding transaction is the caller's — the
        request, the invoice status and the notification all live in it.

        The constraint on (request_id, position) is what genuinely serialises two
        approvers on the same rung. Checking first and inserting second would
        leave a window between the two in which both see it free.
        """
        try:
            async with self.db.begin_nested():
                await self.repo.add_decision(
                    request_id=request.id,
                    position=position,
                    decided_by=user_id,
                    decision=decision,
                    reason=(reason or None),
                )
        except IntegrityError as exc:
            raise ConflictError(
                "Somebody else has already decided this step.",
                code="APPROVAL_RACED",
            ) from exc

    async def _close(
        self,
        request: ApprovalRequest,
        invoice: MatchHistory,
        *,
        status: ApprovalRequestStatus,
        type: NotificationType,
        title: str,
        message: str | None,
    ) -> ApprovalRequest:
        """End a request and give the invoice its old status back.

        `status_before_approval`, not PENDING_REVIEW. An invoice at PO_CREATED
        has a real draft order sitting in Odoo; rewinding it to a review queue
        would leave this row claiming something the system of record does not
        agree with, and would put a reviewer in front of work already done.

        A decline's reason goes on the decision row and NOT on
        `rejection_reason`, which means something else entirely — that the
        invoice was thrown out, rather than sent back for another go.
        """
        await self.repo.update_request(request, status=status)
        await self.invoices.update(invoice, status=request.status_before_approval)

        if request.requested_by is not None:
            await self.notifications.notify_user(
                user_id=request.requested_by,
                company_id=invoice.company_id,
                type=type,
                title=title,
                message=message,
                match_history_id=invoice.id,
            )
        return request

    async def _notify_step(
        self,
        request: ApprovalRequest,
        *,
        position: int,
        invoice: MatchHistory,
        already_decided: set[uuid.UUID],
    ) -> None:
        """Tell the people whose turn it now is.

        `notify_user` per approver rather than `notify_admins`: the recipients
        come from this request's snapshot, and `notify_admins` resolves the ADMIN
        role — which would tell the wrong people it was their turn and tell the
        right ones nothing.

        `already_decided` is passed in rather than read off `request.decisions`.
        That collection is lazy="raise", and a request created moments ago has
        never loaded it — so reading it here would raise on the one path where
        the answer is obviously "nobody".
        """
        step = step_at(request, position)
        if step is None:
            return

        recipients = approvers_of(step)
        if not request.allow_self_approval and request.requested_by is not None:
            # No point telling somebody it is their turn when the rule says they
            # may not act on it.
            recipients.discard(request.requested_by)
        recipients -= already_decided

        if not recipients:
            logger.warning(
                "approval.step_has_no_reachable_approver",
                extra={
                    "request_id": str(request.id),
                    "position": position,
                    "company_id": str(invoice.company_id),
                },
            )
            return

        for user_id in sorted(recipients):
            await self.notifications.notify_user(
                user_id=user_id,
                company_id=invoice.company_id,
                type=NotificationType.APPROVAL_REQUESTED,
                title=f"Your approval is needed: {invoice.file_name}",
                message=f"Step {position} — {step['name']}.",
                match_history_id=invoice.id,
            )
