"""let an approval step record the goods receipt

Revision ID: f2d7e51a9c38
Revises: e1c6048d2f75
Create Date: 2026-08-20 11:00:00.000000

Closes the leg of the three-way match this system was taking on trust.

Accounts payable's classic control is purchase order <-> goods receipt <->
invoice. This system already matched the invoice to the order, and Odoo already
knew what had been received — but nobody ever confirmed the goods actually
arrived. `remaining_to_bill` is deliberately ordered-minus-invoiced, so
`qty_received` was shown to the reviewer and then ignored by every rule.

A step marked `records_receipt` turns its approval into the receipt itself: the
person looking at ordered/received/remaining is the person whose click posts the
stock move. The step stops being a formality that unblocks somebody else and
becomes the control.

Three columns:

* `approval_steps.records_receipt` — which rung does it.
* `approval_requests.po_id` — which order to receive against. The lines snapshot
  holds `po_line_id`s but never the order they belong to, and re-deriving it
  from the invoice at approval time would read a field a reviewer may have
  changed since.
* `approval_requests.receipt` — what Odoo actually did, so billing knows not to
  do it again and a human can reconcile the picking by name.

All three are nullable or defaulted, so this changes nothing for a chain that
does not use them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2d7e51a9c38"
down_revision: str | None = "e1c6048d2f75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approval_steps",
        sa.Column(
            "records_receipt",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # Nullable, and the service treats a missing one as "this request cannot
    # record a receipt" with a message saying so. Requests written before this
    # migration have no order recorded and there is nothing honest to backfill
    # it with.
    op.add_column(
        "approval_requests",
        sa.Column("po_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "approval_requests",
        sa.Column(
            "receipt",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("approval_requests", "receipt")
    op.drop_column("approval_requests", "po_id")
    op.drop_column("approval_steps", "records_receipt")
