"""approval chains, steps, requests and decisions

Revision ID: d0b5f37c1e64
Revises: c9a4e26b0d53
Create Date: 2026-08-20 10:02:00.000000

Step 3 of 4 adding configurable approval chains.

Four tables in two pairs. `chains`/`steps` are the policy — edited freely.
`requests`/`decisions` are the record of what happened to one invoice, and are
never edited at all: a request carries its own copy of the steps it began with,
so an admin reorganising the policy cannot change who still has to approve
something already in motion.

Adds only new tables and two new enum types, so it changes no behaviour on its
own. Nothing is gated until a company activates a chain, and step 4 seeds every
chain inactive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d0b5f37c1e64"
down_revision: str | None = "c9a4e26b0d53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Created explicitly below and then referenced with create_type=False, rather
# than letting create_table emit them: the second table to mention a type would
# otherwise try to CREATE TYPE it again.
_REQUEST_STATUS = postgresql.ENUM(
    "pending",
    "approved",
    "declined",
    "cancelled",
    name="approval_request_status",
    create_type=False,
)
_DECISION = postgresql.ENUM(
    "approved",
    "declined",
    "cancelled",
    name="approval_decision",
    create_type=False,
)
# Already exists and belongs to match_history. Referenced, never created.
_INVOICE_STATUS = postgresql.ENUM(name="invoice_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    _REQUEST_STATUS.create(bind, checkfirst=True)
    _DECISION.create(bind, checkfirst=True)

    # ---------------------------------------------------------------- policy
    op.create_table(
        "approval_chains",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        # Inactive by default. Activating blocks every bill in the company until
        # the steps are decided, so it is that company admin's deliberate act
        # and never a side effect of deploying this.
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "allow_self_approval",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_chains")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_approval_chains_company_id_companies"),
            # RESTRICT like every other company reference: companies are
            # suspended, never deleted.
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_approval_chains_company_active",
        "approval_chains",
        ["company_id", "is_active"],
    )
    # The real "one active chain" rule. A partial unique index rather than a
    # service check, because two admins activating two chains in the same second
    # is precisely the case a service check loses.
    op.create_index(
        "uq_approval_chains_one_active",
        "approval_chains",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "approval_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("chain_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        # No foreign key is possible from inside an array, so "these are real,
        # active users of this company" is checked in the service when a chain is
        # saved — the moment it can still be fixed without an invoice stuck in it.
        sa.Column(
            "approver_user_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_steps")),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["approval_chains.id"],
            name=op.f("fk_approval_steps_chain_id_approval_chains"),
            # CASCADE, unlike most keys here: a step has no meaning without its
            # chain and no audit value. What must survive is the request, and
            # that keeps its own copy.
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "chain_id", "position", name="uq_approval_steps_chain_position"
        ),
    )
    op.create_index(
        op.f("ix_approval_steps_chain_id"), "approval_steps", ["chain_id"]
    )

    # ---------------------------------------------------------------- record
    op.create_table(
        "approval_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chain_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", _REQUEST_STATUS, nullable=False),
        # Its own column, NOT derived from match_history.reviewed_by — that is a
        # single last-writer slot which confirm, reject, create-po and
        # create-bill all overwrite in turn, so by billing time it holds the
        # biller. A self-approval rule built on it would compare the wrong two
        # people and pass.
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "current_position", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        # max(invoice_total, proposed_total). The larger on purpose: the invoice
        # total is read off the document by OCR and is not trustworthy, while the
        # proposed total comes from Odoo's own unit prices and is.
        sa.Column("amount_at_request", sa.Numeric(18, 4), nullable=True),
        # Frozen with the steps: an admin turning this on mid-flight must not
        # retroactively let the requester approve their own rung.
        sa.Column(
            "allow_self_approval",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        # Restored when the chain finishes or is declined. Rewinding to
        # pending_review unconditionally would be wrong: an invoice at po_created
        # has a real draft order in Odoo, and this row must not disagree with it.
        sa.Column("status_before_approval", _INVOICE_STATUS, nullable=False),
        sa.Column(
            "steps_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "lines_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_approval_requests_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["match_history.id"],
            name=op.f("fk_approval_requests_invoice_id_match_history"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["approval_chains.id"],
            name=op.f("fk_approval_requests_chain_id_approval_chains"),
            # RESTRICT: a chain that has been used is part of the audit trail.
            # Editing it is fine — travelling requests hold their own snapshot —
            # but deleting it would orphan the record of what was agreed.
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_approval_requests_requested_by_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_approval_requests_company_status",
        "approval_requests",
        ["company_id", "status"],
    )
    op.create_index(
        "ix_approval_requests_invoice", "approval_requests", ["invoice_id"]
    )
    # One open request per invoice. Without it, two people clicking "request
    # approval" produce two chains for one bill and whichever finishes first
    # silently authorises it.
    op.create_index(
        "uq_approval_requests_one_open",
        "approval_requests",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "approval_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        # SET NULL, not CASCADE. Deleting a user must not quietly erase the fact
        # that somebody approved a payment.
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision", _DECISION, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # No updated_at, and no update path in the service: a decision is an
        # event. An updated_at would advertise an edit that must never happen.
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_decisions")),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["approval_requests.id"],
            name=op.f("fk_approval_decisions_request_id_approval_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_approval_decisions_decided_by_users"),
            ondelete="SET NULL",
        ),
        # The concurrency guard, and the reason a step cannot be skipped. Two
        # approvers on one rung clicking together would otherwise each read the
        # same current_position, each write a decision, and each advance it —
        # landing two rungs on and silently skipping the one between. Here the
        # second INSERT simply fails.
        sa.UniqueConstraint(
            "request_id", "position", name="uq_approval_decisions_request_position"
        ),
    )
    op.create_index(
        op.f("ix_approval_decisions_request_id"), "approval_decisions", ["request_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_approval_decisions_request_id"), "approval_decisions")
    op.drop_table("approval_decisions")

    op.drop_index("uq_approval_requests_one_open", "approval_requests")
    op.drop_index("ix_approval_requests_invoice", "approval_requests")
    op.drop_index("ix_approval_requests_company_status", "approval_requests")
    op.drop_table("approval_requests")

    op.drop_index(op.f("ix_approval_steps_chain_id"), "approval_steps")
    op.drop_table("approval_steps")

    op.drop_index("uq_approval_chains_one_active", "approval_chains")
    op.drop_index("ix_approval_chains_company_active", "approval_chains")
    op.drop_table("approval_chains")

    bind = op.get_bind()
    _DECISION.drop(bind, checkfirst=True)
    _REQUEST_STATUS.drop(bind, checkfirst=True)
    # invoice_status is not dropped: it was here before this migration and every
    # invoice still uses it.
