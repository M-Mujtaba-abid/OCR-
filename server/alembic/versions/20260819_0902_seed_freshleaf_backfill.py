"""create FreshLeaf and put every existing row under it

Revision ID: e3c6a2d95b18
Revises: d2b5f1c84e07
Create Date: 2026-08-19 09:02:00.000000

Step 3 of 4, and the only one that touches data. Everything in this database
today belongs to one business, so it becomes one company row and every user,
invoice, notification and batch is pointed at it.

Nothing here touches the Odoo credentials. They stay in the environment, where
they work now and will keep working: the per-company Odoo config is read with
the environment as its fallback, so FreshLeaf keeps connecting exactly as it
does today until somebody deliberately moves those credentials into the
database. A migration is the wrong place to handle a secret — it has no
encryption key and it would write the plaintext into the schema history.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e3c6a2d95b18"
down_revision: str | None = "d2b5f1c84e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Fixed rather than generated, so re-running this against a fresh copy of the
#: database produces the same id and any dump taken before it still lines up.
FRESHLEAF_ID = "0f5a2e64-4d2b-4c1e-9a37-6b8c0d1e2f30"
FRESHLEAF_SLUG = "freshleaf"
FRESHLEAF_NAME = "FreshLeaf"

_SCOPED_TABLES = ("match_history", "notifications", "processing_batches")


def upgrade() -> None:
    connection = op.get_bind()

    # Guard, not decoration. This migration is only correct because every row
    # in the database belongs to one business — which is true today because
    # nothing ever passed a tenant. If a second tenant somehow exists, folding
    # both into FreshLeaf would merge two companies' payables, and that is not
    # something to discover afterwards.
    for table in _SCOPED_TABLES:
        tenants = connection.exec_driver_sql(
            f"SELECT DISTINCT tenant_id FROM {table}"  # noqa: S608 — fixed names
        ).scalars().all()
        if len(tenants) > 1:
            raise RuntimeError(
                f"{table} holds more than one tenant_id ({sorted(tenants)}). "
                "This migration assumes a single-tenant database and would "
                "merge them. Split them by hand first."
            )

    op.execute(
        f"""
        INSERT INTO companies (id, name, slug, is_active)
        VALUES ('{FRESHLEAF_ID}', '{FRESHLEAF_NAME}', '{FRESHLEAF_SLUG}', true)
        ON CONFLICT (slug) DO NOTHING
        """
    )

    # Resolved by slug rather than by the constant, so a database where the
    # company was already created by hand backfills to THAT row instead of
    # silently leaving every invoice unclaimed.
    for table in (*_SCOPED_TABLES, "users"):
        op.execute(
            f"""
            UPDATE {table}
               SET company_id = (SELECT id FROM companies WHERE slug = '{FRESHLEAF_SLUG}')
             WHERE company_id IS NULL
            """  # noqa: S608 — table names are the fixed tuple above
        )


def downgrade() -> None:
    # Unclaim the rows before removing the company, or the RESTRICT foreign key
    # refuses — which is the constraint doing its job.
    for table in (*_SCOPED_TABLES, "users"):
        op.execute(
            f"""
            UPDATE {table}
               SET company_id = NULL
             WHERE company_id = (
                   SELECT id FROM companies WHERE slug = '{FRESHLEAF_SLUG}')
            """  # noqa: S608 — table names are the fixed tuple above
        )
    op.execute(f"DELETE FROM companies WHERE slug = '{FRESHLEAF_SLUG}'")
