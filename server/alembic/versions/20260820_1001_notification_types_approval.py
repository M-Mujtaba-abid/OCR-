"""add the three approval notification types

Revision ID: c9a4e26b0d53
Revises: b8f3d15a9c42
Create Date: 2026-08-20 10:01:00.000000

Step 2 of 4 adding configurable approval chains.

Separate from the table migration for the same reason as the invoice_status
label before it: ADD VALUE may share a transaction with anything except a use of
the label it adds, and keeping the declarations alone makes that impossible to
get wrong later.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9a4e26b0d53"
down_revision: str | None = "b8f3d15a9c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LABELS = ("approval_requested", "approval_granted", "approval_declined")


def upgrade() -> None:
    for label in _LABELS:
        op.execute(
            f"ALTER TYPE notification_type ADD VALUE IF NOT EXISTS '{label}'"
        )


def downgrade() -> None:
    # Enum values cannot be dropped in Postgres. Unused after a downgrade.
    pass
