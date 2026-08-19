"""make company_id mandatory

Revision ID: f4d7b3ea6c29
Revises: e3c6a2d95b18
Create Date: 2026-08-19 09:03:00.000000

Step 4 of 4. Until now a row with no company was merely unusual; from here it
is impossible. That is the whole point of the exercise — an unscoped row is one
that no company-scoped query returns and that no company owns.

`users` is the exception, and only for the platform owner, who belongs to no
company. A check constraint says so, rather than the column simply being
nullable and everyone hoping it means what they think.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f4d7b3ea6c29"
down_revision: str | None = "e3c6a2d95b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPED_TABLES = ("match_history", "notifications", "processing_batches")


def upgrade() -> None:
    for table in _SCOPED_TABLES:
        op.alter_column(table, "company_id", nullable=False)

    # `role::text`, not `role = 'super_admin'`. Alembic runs the whole upgrade
    # in ONE transaction, and Postgres refuses to use an enum label in the same
    # transaction that added it — which step 1 did. Comparing the cast to a
    # plain string never names an enum value, so this holds whether the four
    # steps run together or apart.
    op.create_check_constraint(
        "user_belongs_to_company",
        "users",
        "(company_id IS NOT NULL) OR (role::text = 'super_admin')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_users_user_belongs_to_company"), "users", type_="check"
    )
    for table in _SCOPED_TABLES:
        op.alter_column(table, "company_id", nullable=True)
