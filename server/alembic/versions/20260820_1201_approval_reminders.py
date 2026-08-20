"""track how long a rung has been waiting, and when it was last nudged

Revision ID: b4f9a73c1e52
Revises: a3e8f62b0d41
Create Date: 2026-08-20 12:01:00.000000

An approval chain's real failure mode is not being refused. It is being
forgotten: a request sits on somebody who is on leave, and because nothing in
the product ever mentions it again, the invoice is discovered weeks later by
whoever chases the vendor. The gate makes billing wait; nothing so far made
anybody notice.

Two columns are enough to fix that without a scheduler that has to remember
anything:

* `current_step_since` — when this rung started waiting. Reset every time the
  request advances, so it measures the CURRENT approver's silence rather than
  the age of the whole request. Derivable from the decisions table, but only
  through a correlated subquery, and the sweep's WHERE clause has to be
  something an index can answer.

* `reminded_at` — when a nudge last went out, so a sweep running every five
  minutes does not send a notification every five minutes. Cleared on advance:
  a rung that took three days to decide must not make the next approver's first
  notification a reminder.

The partial index is the point of the pair. The sweep runs constantly and looks
for a needle that is almost always absent, so it must not read the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4f9a73c1e52"
down_revision: str | None = "a3e8f62b0d41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a server default, so requests already in flight start their
    # clock at the migration rather than looking infinitely overdue and firing a
    # nudge for every one of them on the next sweep.
    op.add_column(
        "approval_requests",
        sa.Column(
            "current_step_since",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "approval_requests",
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Partial, and deliberately so. Only pending requests can be overdue, and
    # they are a small minority of the table — an unrestricted index would be
    # mostly rows the sweep can never return.
    op.create_index(
        "ix_approval_requests_pending_since",
        "approval_requests",
        ["current_step_since"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_approval_requests_pending_since", "approval_requests")
    op.drop_column("approval_requests", "reminded_at")
    op.drop_column("approval_requests", "current_step_since")
