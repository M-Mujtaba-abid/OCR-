"""add company_id to every company-scoped table, nullable for now

Revision ID: d2b5f1c84e07
Revises: c1a4e0b73f21
Create Date: 2026-08-19 09:01:00.000000

Step 2 of 4. Nullable on purpose: the columns have to exist before anything can
be written into them, and the rows that exist today have no company until step
3 puts them in one. Step 4 makes it mandatory.

`tenant_id` is untouched and stays for now. Both columns are populated and
agree from step 3 onward; the old one goes once the last reader has moved off
it, which is a code change and therefore a later migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d2b5f1c84e07"
down_revision: str | None = "c1a4e0b73f21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: RESTRICT everywhere. A company is suspended, never deleted — this is what
#: makes that a rule the database keeps rather than one people remember.
_SCOPED_TABLES = ("match_history", "notifications", "processing_batches")
_ALL_TABLES = (*_SCOPED_TABLES, "users")

#: Every table gets an index on `company_id`; not every table gets the SAME
#: one. Where reads are always "this company, that status" — the admin queue —
#: or "this company, that role" — the user list — the composite answers the
#: single-column case too, so a standalone index there would be one nothing
#: ever reaches for and every write still has to maintain.
_COMPANY_INDEXES: dict[str, tuple[str, list[str]]] = {
    "match_history": ("ix_match_history_company_status", ["company_id", "status"]),
    "users": ("ix_users_company_role", ["company_id", "role"]),
    "notifications": ("ix_notifications_company_id", ["company_id"]),
    "processing_batches": (
        "ix_processing_batches_company_id",
        ["company_id"],
    ),
}


def upgrade() -> None:
    for table in _ALL_TABLES:
        op.add_column(
            table,
            sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            op.f(f"fk_{table}_company_id_companies"),
            table,
            "companies",
            ["company_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        name, columns = _COMPANY_INDEXES[table]
        op.create_index(name, table, columns)


def downgrade() -> None:
    for table in _ALL_TABLES:
        op.drop_index(_COMPANY_INDEXES[table][0], table_name=table)
        op.drop_constraint(
            op.f(f"fk_{table}_company_id_companies"), table, type_="foreignkey"
        )
        op.drop_column(table, "company_id")
