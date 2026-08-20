"""add pending_approval to the invoice_status enum

Revision ID: b8f3d15a9c42
Revises: a7e2c94f8b31
Create Date: 2026-08-20 10:00:00.000000

Step 1 of 4 adding configurable approval chains.

An invoice waiting on people is a state the machine had no word for. `confirmed`
claims the work is done and the bill can be raised; `pending_review` sends it
back to a queue where somebody would re-review something already reviewed.

Deliberately a detour rather than a step: the status the invoice held on the way
in is recorded on the approval request and restored when the chain finishes,
whichever way it goes. Nothing downstream should treat this as a resting place.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b8f3d15a9c42"
down_revision: str | None = "a7e2c94f8b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Its own migration, and first. Postgres 12+ permits ADD VALUE inside a
    # transaction only while the label goes unused in that same transaction —
    # so declaring it here keeps every later migration free to reference it.
    op.execute(
        "ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS 'pending_approval'"
    )


def downgrade() -> None:
    # Postgres cannot drop a value from an enum. Undoing this properly means
    # rebuilding the type and rewriting every row that uses it — destructive,
    # and pointless for a label that is simply unused after a downgrade.
    pass
