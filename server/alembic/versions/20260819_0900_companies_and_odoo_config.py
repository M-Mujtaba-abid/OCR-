"""companies, their Odoo config, and the platform-owner role

Revision ID: c1a4e0b73f21
Revises: b41f5c0a7d92
Create Date: 2026-08-19 09:00:00.000000

Step 1 of 4 turning one company's system into a platform. This one only adds
new things — two tables and an enum label — so it changes no behaviour and can
ship on its own.

The three steps after it add `company_id` to the existing tables, put every row
that exists today under one company, and then make that mandatory.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1a4e0b73f21"
down_revision: str | None = "b41f5c0a7d92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        # Unique because object storage keys are built from it: two companies
        # sharing a slug would share a prefix, which is the one thing the
        # prefix exists to prevent.
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("slug", name=op.f("uq_companies_slug")),
    )

    op.create_table(
        "company_odoo_config",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("database", sa.String(length=120), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        # Opaque text: which cipher produced it is the encryption helper's
        # business, not the schema's. Never the plaintext key.
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_odoo_config_company_id_companies"),
            # CASCADE here alone: the config is part of the company rather than
            # a record of its own, so it has no meaning once the company is
            # gone. Everything ELSE pointing at a company is RESTRICT.
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_odoo_config")),
        sa.UniqueConstraint(
            "company_id", name=op.f("uq_company_odoo_config_company_id")
        ),
    )

    # Declared here and USED four migrations later, deliberately apart: Postgres
    # refuses to use a new enum label in the transaction that added it, and
    # Alembic runs a whole upgrade in one transaction. The check constraint in
    # step 4 compares `role::text` for the same reason.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'super_admin'")


def downgrade() -> None:
    op.drop_table("company_odoo_config")
    op.drop_table("companies")
    # Postgres cannot drop a value from an enum. Rebuilding the type to remove
    # one unused label would rewrite every user row for no gain.
