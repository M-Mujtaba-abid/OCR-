"""add the approval_overdue notification type

Revision ID: a3e8f62b0d41
Revises: f2d7e51a9c38
Create Date: 2026-08-20 12:00:00.000000

Its own type rather than reusing `approval_requested`, because it is addressed
to somebody else entirely. A nudge to the approver is the same ask repeated —
"your approval is needed" is still true. An escalation goes to the company's
administrators, and telling them their approval is needed would be false: it is
not their step. What they are being told is that one has been sitting.

Alone in its own migration for the usual reason: ADD VALUE may share a
transaction with anything except a use of the label it adds.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a3e8f62b0d41"
down_revision: str | None = "f2d7e51a9c38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'approval_overdue'"
    )


def downgrade() -> None:
    # Enum values cannot be dropped in Postgres. Unused after a downgrade.
    pass
