"""Approval chains — who has to say yes before an invoice becomes a bill.

Until now the only approval control in this system was the permission split
between `invoice.review` and `invoice.bill`: a fixed two-step chain, expressed
in Python, changeable only by deploying. These four tables move that decision
into data so a company can describe its own — a manager asks, the person who
checked the parcel approves, the admin approves, and only then does anything
reach Odoo.

The shape is deliberately four tables rather than a status column with a
counter:

  chains -> steps            what the company decided, editable
  requests -> decisions      what actually happened to one invoice, immutable

The split matters because the two have different lifetimes. A chain is edited;
a request must never change underneath an invoice already travelling through
it. That is what `steps_snapshot` is for — see `ApprovalRequest`.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.company import CompanyScopedMixin
from app.models.match_history import InvoiceStatus

if TYPE_CHECKING:
    from app.models.user import User


class ApprovalRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    #: An admin pulled the request out of a chain nobody could satisfy — every
    #: named approver deactivated, say. Distinct from DECLINED because nobody
    #: judged the invoice; the chain itself failed.
    CANCELLED = "cancelled"


class ApprovalDecision(str, enum.Enum):
    APPROVED = "approved"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class ApprovalChain(UUIDPrimaryKeyMixin, CompanyScopedMixin, TimestampMixin, Base):
    """One company's approval policy.

    At most one may be active at a time — enforced by a partial unique index
    below, not by convention. With two active chains every request would have to
    answer "which one applied", and the honest answer would depend on row order.
    """

    __tablename__ = "approval_chains"
    __table_args__ = (
        # The list screen reads every chain for one company; the gate reads only
        # the active one. Both are served by this.
        Index("ix_approval_chains_company_active", "company_id", "is_active"),
        # The real constraint. A partial unique index rather than application
        # logic: two admins activating two chains in the same second is exactly
        # the case application logic loses.
        Index(
            "uq_approval_chains_one_active",
            "company_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: Inactive by default, and the seed migration leans on that. Switching a
    #: company to chained approval blocks every bill until somebody decides the
    #: steps, so it has to be a deliberate act by that company's admin rather
    #: than something a deploy does to them overnight.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: Off by default: whoever asks for the bill should not be one of the people
    #: approving it. The escape exists for the genuinely one-admin company,
    #: where the alternative is a chain that can never complete.
    allow_self_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    steps: Mapped[list["ApprovalStep"]] = relationship(
        back_populates="chain",
        cascade="all, delete-orphan",
        order_by="ApprovalStep.position",
        # Every read of a chain wants its steps — there is no such thing as a
        # useful chain without them — but eager loading is still opt-in per
        # query so a list screen can decide otherwise.
        lazy="raise",
    )


class ApprovalStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One rung. Any one of `approver_user_ids` may decide it.

    A list rather than a single user, even though the business described a
    single person. One human is a single point of failure: illness, leave or a
    resignation strands every invoice on that rung, and the only way out is a
    hand-written UPDATE. A list of eligible people is the same configuration
    effort and cannot deadlock.

    No company_id: a step is reachable only through its chain, which has one.
    """

    __tablename__ = "approval_steps"
    __table_args__ = (
        # Positions are the identity of a step within its chain, so two rungs
        # cannot share one. This is also what makes `steps_snapshot` indexable
        # by position without ambiguity.
        UniqueConstraint("chain_id", "position", name="uq_approval_steps_chain_position"),
    )

    chain_id: Mapped[uuid.UUID] = mapped_column(
        # CASCADE, unlike most foreign keys here: a step has no meaning without
        # its chain and carries no audit value. What must survive is the
        # *request*, and that keeps its own copy.
        ForeignKey("approval_chains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: 1-based, and contiguous by convention rather than by constraint. The
    #: service rewrites the whole set when a chain is edited, so gaps never
    #: arise from normal use.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: No foreign key — Postgres cannot enforce one from inside an array. The
    #: check that these are real, active users of this company therefore lives
    #: in the service and runs when a chain is saved, which is the moment it can
    #: still be fixed cheaply.
    approver_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )

    chain: Mapped["ApprovalChain"] = relationship(back_populates="steps", lazy="raise")


class ApprovalRequest(UUIDPrimaryKeyMixin, CompanyScopedMixin, TimestampMixin, Base):
    """One invoice's journey through one chain.

    A new row per submission. A declined request is never reopened — the next
    attempt starts a fresh one at position 1 — which keeps `approval_decisions`
    honest: one decision per position per request, enforced by a unique
    constraint, with no history to reconcile.
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        # "What is waiting in this company" — the queue behind the badge.
        Index("ix_approval_requests_company_status", "company_id", "status"),
        Index("ix_approval_requests_invoice", "invoice_id"),
        # One open request per invoice. Without this, two people clicking
        # "request approval" produce two chains for one bill and whichever
        # finishes first silently authorises it.
        Index(
            "uq_approval_requests_one_open",
            "invoice_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match_history.id", ondelete="CASCADE"), nullable=False
    )

    #: RESTRICT: a chain that has been used is part of the audit trail. Editing
    #: it is fine — travelling requests hold their own snapshot — but deleting
    #: it would orphan the record of what was agreed.
    chain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_chains.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[ApprovalRequestStatus] = mapped_column(
        Enum(
            ApprovalRequestStatus,
            name="approval_request_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ApprovalRequestStatus.PENDING,
    )

    #: Who asked. Its own column, and NOT derived from `MatchHistory.reviewed_by`
    #: — that is a single last-writer slot which confirm, reject, create-po and
    #: create-bill all overwrite in turn. By the time a bill is created it holds
    #: the biller, so a self-approval rule built on it would compare the wrong
    #: two people and pass.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    #: The rung awaiting a decision. Advanced only after the decision row for
    #: the current position has been inserted, so the unique constraint on
    #: (request_id, position) is what actually serialises two simultaneous
    #: approvers rather than this integer.
    current_position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    #: `max(invoice_total, proposed_total)` at the moment of asking.
    #:
    #: The larger of the two on purpose. `invoice_total` is read off the document
    #: by OCR and is not trustworthy; `proposed_total` is computed from the Odoo
    #: order's own unit prices and is. Recording the maximum means an
    #: under-read document cannot make the approved amount look smaller than
    #: what was really on the table.
    amount_at_request: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    #: What the invoice's status was before it entered the chain, restored when
    #: the chain finishes or is declined.
    #:
    #: Sending a declined invoice to PENDING_REVIEW unconditionally would be
    #: wrong: an invoice at PO_CREATED has a real draft order sitting in Odoo,
    #: and rewinding the status here would leave this row disagreeing with the
    #: system of record.
    status_before_approval: Mapped[InvoiceStatus] = mapped_column(
        # postgresql.ENUM rather than sa.Enum purely for `create_type=False`:
        # `invoice_status` already exists and belongs to `match_history`. Without
        # this, metadata-driven creation would try to CREATE TYPE a second time.
        ENUM(
            InvoiceStatus,
            name="invoice_status",
            values_callable=lambda e: [m.value for m in e],
            create_type=False,
        ),
        nullable=False,
    )

    #: The chain's self-approval policy, frozen with the steps.
    #:
    #: Read from here rather than from `chain.allow_self_approval` for the same
    #: reason the steps are snapshotted: an admin turning the flag on while a
    #: request is mid-flight would otherwise let the person who asked approve
    #: their own rung, retroactively, on a request that started under the
    #: stricter rule.
    allow_self_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: The steps exactly as they stood when this request began:
    #: `[{"position": 1, "name": "...", "approver_user_ids": ["..."]}, ...]`
    #:
    #: Frozen for the same reason `extra["odoo_bill"]` records the line mapping
    #: actually used rather than re-deriving it: an admin editing the chain
    #: while invoices are mid-flight must not change who still has to approve
    #: something already in motion. Every eligibility check reads this, never
    #: `approval_steps`.
    steps_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    #: The bill lines as approved: `[{"po_line_id": 1, "quantity": 2.0}, ...]`.
    #:
    #: This is the difference between approving an invoice and approving an
    #: amount. Quantities are editable right up to the moment the bill is
    #: submitted, so without a record of what was agreed, a request approved at
    #: one figure can be billed at another. `check_exceeds_approval` compares
    #: the submitted lines against this.
    lines_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    decisions: Mapped[list["ApprovalDecisionRecord"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ApprovalDecisionRecord.position",
        lazy="raise",
    )

    requester: Mapped["User | None"] = relationship(
        foreign_keys=[requested_by], lazy="raise"
    )


class ApprovalDecisionRecord(UUIDPrimaryKeyMixin, Base):
    """One person's answer on one rung. Append-only.

    No TimestampMixin and no update path anywhere in the service: a decision is
    an event, and an `updated_at` on it would advertise an edit that must never
    happen. Changing your mind means a new request, which is also the only
    version of that story an auditor can follow.

    Named `ApprovalDecisionRecord` rather than `ApprovalDecision` because that
    name belongs to the enum above — the value it stores.
    """

    __tablename__ = "approval_decisions"
    __table_args__ = (
        # The concurrency guard, and the reason step-skipping is impossible.
        #
        # Two approvers on the same rung clicking at the same moment would
        # otherwise each read current_position = 2, each write a decision, and
        # each advance it — landing on 4 and silently skipping rung 3. Here the
        # second INSERT simply fails.
        UniqueConstraint(
            "request_id", "position", name="uq_approval_decisions_request_position"
        ),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    #: SET NULL, not CASCADE. Deleting a user must not quietly erase the fact
    #: that somebody approved a payment.
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    decision: Mapped[ApprovalDecision] = mapped_column(
        Enum(
            ApprovalDecision,
            name="approval_decision",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    #: Why. Required by the service on a decline — "no" without a reason sends
    #: the invoice back to somebody who cannot act on it.
    reason: Mapped[str | None] = mapped_column(Text)

    decided_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    request: Mapped["ApprovalRequest"] = relationship(
        back_populates="decisions", lazy="raise"
    )
    decider: Mapped["User | None"] = relationship(
        foreign_keys=[decided_by], lazy="raise"
    )
