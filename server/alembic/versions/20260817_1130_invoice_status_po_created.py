"""add po_created to the invoice_status enum

Revision ID: b41f5c0a7d92
Revises: 3e7d82c89bb3
Create Date: 2026-08-17 11:30:00.000000

An invoice that matched nothing can now become a draft purchase order in Odoo,
which is an outcome the state machine had no word for. `no_match` said the work
was unfinished; `confirmed` would claim an order existed beforehand. Neither is
true of an order this system created.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b41f5c0a7d92'
down_revision: str | None = '3e7d82c89bb3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows ADD VALUE inside a transaction as long as the label
    # is not USED in the same one. This migration only declares it, so the
    # transaction Alembic wraps around it is fine.
    op.execute("ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS 'po_created'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum. Undoing this properly means
    # rebuilding the type and rewriting every row that uses it — destructive,
    # and pointless for a label that is simply unused after a downgrade.
    pass
