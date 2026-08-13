"""Declarative base and shared column mixins."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, MetaData, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic autogenerate can DROP them later
# instead of emitting a migration that references a name Postgres invented.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Decided once here so no model has to remember that timestamps carry a
    # timezone or that money is never a float.
    type_annotation_map = {
        uuid.UUID: UUID(as_uuid=True),
        dt.datetime: DateTime(timezone=True),
        Decimal: Numeric(18, 4),
        dict[str, Any]: JSONB,
        list[dict[str, Any]]: JSONB,
        str: String,
    }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} id={getattr(self, 'id', None)}>"


class UUIDPrimaryKeyMixin:
    """UUID primary keys rather than serial integers.

    User ids appear in JWT `sub` claims and in URLs. Sequential integers leak
    volume and make enumeration trivial to attempt.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
