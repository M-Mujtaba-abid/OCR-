"""drop tenant_id — company_id is the only scope now

Revision ID: a7e2c94f8b31
Revises: f4d7b3ea6c29
Create Date: 2026-08-19 14:00:00.000000

The last step of the tenancy migration. Both columns have been populated and in
agreement since the backfill; this removes the one nothing reads any more.

Deliberately a SEPARATE migration from the code change that stopped reading it,
and deliberately last. `tenant_id` had to outlive its readers by one deploy: a
rolling release runs old and new instances at once, and dropping a NOT NULL
column an old instance still writes takes that instance down mid-request.

The indexes go with it. `ix_match_history_tenant_status` was the admin queue's
composite; `ix_match_history_company_status`, created in step 2, is the same
shape over the column that replaced it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7e2c94f8b31"
down_revision: str | None = "f4d7b3ea6c29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("match_history", "notifications", "processing_batches")


def upgrade() -> None:
    op.drop_index("ix_match_history_tenant_status", table_name="match_history")
    op.drop_index("ix_match_history_tenant", table_name="match_history")
    for table in _TABLES:
        op.drop_column(table, "tenant_id")


def downgrade() -> None:
    """Restores the column and its content, not merely its shape.

    Every row goes back to 'default', which is what it held before the
    backfill — this database only ever had the one tenant, so that is the true
    value rather than a placeholder.
    """
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "tenant_id",
                sa.String(length=64),
                nullable=False,
                server_default="default",
            ),
        )
    op.create_index("ix_match_history_tenant", "match_history", ["tenant_id"])
    op.create_index(
        "ix_match_history_tenant_status", "match_history", ["tenant_id", "status"]
    )
