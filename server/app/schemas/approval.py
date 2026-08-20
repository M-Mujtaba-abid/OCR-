"""Approval chain request/response schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.approval import ApprovalDecision, ApprovalRequestStatus
from app.models.match_history import InvoiceStatus
from app.schemas.invoice import CreateBillLine, UploaderRead


# ---------------------------------------------------------------------------
# Chains — the policy
# ---------------------------------------------------------------------------
class ApprovalStepInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    records_receipt: bool = Field(
        default=False,
        description=(
            "Approving this step posts the goods receipt in Odoo for the "
            "approved quantities. At most one step per chain may do it. Note "
            "the stock movement is real from that moment and a later decline "
            "does not return it — a receipt is a statement about the warehouse, "
            "not about whether the invoice gets paid."
        ),
    )
    approver_user_ids: list[uuid.UUID] = Field(
        min_length=1,
        description=(
            "Anybody on this list may decide the step. A list rather than one "
            "person because one person is a single point of failure: illness or "
            "a resignation would strand every invoice on this rung."
        ),
    )

    @field_validator("approver_user_ids")
    @classmethod
    def _unique(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        # Naming somebody twice on one rung does not make them decide it twice;
        # it just makes the screen wrong.
        return list(dict.fromkeys(value))


class SaveChainRequest(BaseModel):
    """Create or replace a chain, steps and all.

    Steps arrive as an ordered list and their positions are assigned from that
    order server-side. A client that sent 1, 2, 4 would otherwise create a chain
    whose third rung can never be reached.
    """

    name: str = Field(min_length=1, max_length=120)
    steps: list[ApprovalStepInput] = Field(min_length=1)
    #: Off by default. The escape exists for a genuinely one-admin company,
    #: where the alternative is a chain that can never complete.
    allow_self_approval: bool = False
    #: Activating blocks every bill in the company until each rung is decided,
    #: so it is never the default for a chain being written for the first time.
    is_active: bool = False


class ApprovalStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    name: str
    approver_user_ids: list[uuid.UUID]
    records_receipt: bool = False


class ApprovalChainRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    allow_self_approval: bool
    steps: list[ApprovalStepRead]
    created_at: dt.datetime
    updated_at: dt.datetime


# ---------------------------------------------------------------------------
# Requests — the record
# ---------------------------------------------------------------------------
class RequestApprovalRequest(BaseModel):
    """Ask for approval of a specific bill, not of an invoice in the abstract.

    The same `po_id`/`lines` the create-bill call would carry, and for a reason:
    the lines are priced against Odoo and frozen onto the request, so what the
    approvers see and what the biller may later submit are the same numbers.
    """

    po_id: int = Field(gt=0)
    lines: list[CreateBillLine] = Field(min_length=1)


class DecideRequest(BaseModel):
    approve: bool
    #: Required when declining — enforced in the service rather than here, so
    #: the message can say why rather than naming a field.
    reason: str | None = Field(default=None, max_length=2000)


class CancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    decision: ApprovalDecision
    reason: str | None = None
    decided_at: dt.datetime
    decided_by: uuid.UUID | None = None


class ApprovalLineRead(BaseModel):
    """One line as approved. Straight off `lines_snapshot`.

    Rendered from the snapshot rather than from a fresh preview, and that is
    load-bearing: an approver looking at live Odoo figures would be signing off
    numbers that are not the ones the request is actually capped at.
    """

    po_line_id: int
    quantity: float
    description: str = ""
    unit_price: float = 0.0
    tax_rate: float = 0.0


class ApprovalStepProgress(BaseModel):
    """One rung and what happened to it — what the progress strip renders."""

    position: int
    name: str
    approver_user_ids: list[uuid.UUID]
    records_receipt: bool = False
    #: None while the rung is still waiting.
    decision: ApprovalDecisionRead | None = None
    is_current: bool = False


class ApprovalRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    status: ApprovalRequestStatus
    current_position: int
    amount_at_request: Decimal | None = None
    status_before_approval: InvoiceStatus
    allow_self_approval: bool
    requested_by: uuid.UUID | None = None
    requester: UploaderRead | None = None
    created_at: dt.datetime
    #: When the CURRENT step started waiting — not the age of the request. What
    #: the sweep measures.
    current_step_since: dt.datetime
    #: Whole days the current step has been waiting, computed server-side.
    #:
    #: Sent rather than derived in the browser, and not only to save a
    #: subtraction: a value computed from the client's clock during render is
    #: unstable across re-renders and wrong on a machine whose time is off. This
    #: is the same figure the overdue reminder puts in its notification, so the
    #: screen and the email agree.
    waiting_days: int = 0

    steps: list[ApprovalStepProgress] = Field(default_factory=list)
    lines: list[ApprovalLineRead] = Field(default_factory=list)

    #: The Odoo order these lines belong to.
    po_id: int | None = None
    #: What Odoo did when a receiving step was approved, or null. Its
    #: `picking_name` is what a person reconciles against Odoo.
    receipt: dict[str, Any] | None = None


class InvoiceApprovalRead(BaseModel):
    """Everything the review screen needs about one invoice's approval, at once.

    Carries `chain_active` alongside the request rather than making the client
    ask separately, because the client that most needs the answer cannot get it
    any other way: reading the chain list takes `approval.configure`, which a
    manager does not hold — and a manager with no request yet is exactly the
    person who needs to be told a chain exists and to send this through it.
    """

    #: Whether this company gates billing at all. False means
    #: `create_bill_for_invoice` behaves as it did before the feature existed.
    chain_active: bool
    chain_name: str | None = None
    #: The latest request, whatever became of it. Null if never sent.
    request: ApprovalRequestRead | None = None


class AwaitingItem(BaseModel):
    """One row in the "Awaiting you" queue.

    Carries the invoice's own identifying details rather than only its id: a
    queue that makes you open every row to find out what it is is a queue people
    stop opening.
    """

    request: ApprovalRequestRead
    invoice_id: uuid.UUID
    file_name: str
    vendor: str | None = None
    invoice_no: str | None = None
    step_name: str
    step_position: int
