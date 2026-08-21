"""an index that fits the notification list query

Revision ID: c5a1b84d2f63
Revises: b4f9a73c1e52
Create Date: 2026-08-21 09:00:00.000000

The bell reads `WHERE user_id = ? ORDER BY created_at DESC LIMIT 20`, and until
now nothing indexed that shape. There were two indexes and each served half of
it: `ix_notif_user_unread (user_id, is_read)` matches the filter but says nothing
about order, and `ix_notif_created (created_at)` orders every user's rows
together. So Postgres either filtered and then sorted, or scanned by date and
discarded other people's notifications — both fine on a table of hundreds and
neither fine on one that has never had anything deleted from it.

`(user_id, created_at DESC)` answers the whole query: the leading column filters,
the second is already in the required order, and LIMIT stops the scan twenty rows
in regardless of how many the user has.

DESC in the index rather than relying on a backwards scan. Postgres can read a
btree in either direction, so ASC would also work here — it is written this way
because it matches the query, and an index whose declaration reads like the
ORDER BY it exists for is one nobody has to reason about twice.

Nothing is dropped. `ix_notif_user_unread` is still the right shape for the
unread count, which is the most frequent query in the application, and
`ix_notif_created` starts earning its keep with the retention delete that lands
alongside this.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5a1b84d2f63"
down_revision: str | None = "b4f9a73c1e52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_notif_user_created",
        "notifications",
        # sa.text for the DESC: a plain column name cannot carry a sort
        # direction, and Alembic passes text through to the DDL untouched.
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_notif_user_created", "notifications")
