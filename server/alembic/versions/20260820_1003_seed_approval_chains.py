"""give every company an inactive default chain

Revision ID: e1c6048d2f75
Revises: d0b5f37c1e64
Create Date: 2026-08-20 10:03:00.000000

Step 4 of 4 adding configurable approval chains, and the only one that touches
data.

Every company gets one chain with one step, and it is created INACTIVE. That is
the whole point of the migration: an active chain blocks billing until each rung
has been decided, so switching a business over to chained approval has to be
that business's own decision, made on a screen, and never something a deploy
does to them overnight. Until an admin activates it, `create_bill_for_invoice`
behaves exactly as it does today.

The step is seeded with the company's active admins rather than left empty,
because an empty step is one nobody can satisfy — the chain would be
unactivatable, and the first thing an admin saw would be a validation error
about a row they never created.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e1c6048d2f75"
down_revision: str | None = "d0b5f37c1e64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHAIN_NAME = "Default approval"
STEP_NAME = "Admin approval"


def upgrade() -> None:
    # NOT EXISTS rather than ON CONFLICT: there is no unique key on
    # (company_id) — only the partial one on active chains — so a company that
    # already has a chain by hand must be left with the one it has.
    op.execute(
        f"""
        INSERT INTO approval_chains (company_id, name, is_active, allow_self_approval)
        SELECT c.id, '{CHAIN_NAME}', false, false
          FROM companies c
         WHERE NOT EXISTS (
               SELECT 1 FROM approval_chains ac WHERE ac.company_id = c.id)
        """
    )

    # `role::text` rather than `role = 'admin'`, matching the check constraint on
    # users: comparing an enum column against a bare literal depends on the type
    # still being named what it is today.
    op.execute(
        f"""
        INSERT INTO approval_steps (chain_id, position, name, approver_user_ids)
        SELECT ac.id, 1, '{STEP_NAME}',
               COALESCE(
                   (SELECT array_agg(u.id)
                      FROM users u
                     WHERE u.company_id = ac.company_id
                       AND u.role::text = 'admin'
                       AND u.is_active),
                   '{{}}'::uuid[])
          FROM approval_chains ac
         WHERE ac.name = '{CHAIN_NAME}'
           AND NOT EXISTS (
               SELECT 1 FROM approval_steps s WHERE s.chain_id = ac.id)
        """
    )


def downgrade() -> None:
    # Steps first — their foreign key is CASCADE, but being explicit keeps the
    # order readable — then the chains themselves.
    #
    # A chain that has been used is protected by RESTRICT from
    # approval_requests, so this will refuse rather than orphan a record of what
    # somebody agreed to. That is the constraint doing its job: a database with
    # live approval history is not one this migration can cleanly undo.
    op.execute(
        f"""
        DELETE FROM approval_steps
         WHERE chain_id IN (
               SELECT id FROM approval_chains WHERE name = '{CHAIN_NAME}')
        """
    )
    op.execute(f"DELETE FROM approval_chains WHERE name = '{CHAIN_NAME}'")
