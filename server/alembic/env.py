"""Alembic environment — async variant.

Two things differ from the generated template:

1. The URL comes from app.core.config, never from alembic.ini, so no credential
   lives in a tracked file.
2. Migrations run against the DIRECT Neon endpoint, not the pooled one.
   PgBouncer in transaction mode does not support the session-level operations
   DDL needs, and CREATE TYPE / ALTER TYPE in particular misbehave through it.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings

# Importing the model registry is what populates Base.metadata. Without it
# autogenerate sees an empty schema and cheerfully drops every table.
from app.models import Base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_LIBPQ_ONLY = {"sslmode", "channel_binding", "options", "target_session_attrs"}


def get_migration_url() -> str:
    """Direct (non-pooled) asyncpg URL for DDL."""
    raw = settings.DATABASE_URL.get_secret_value()
    parts = urlsplit(raw)

    # Neon's pooled host differs from the direct one only by "-pooler".
    netloc = parts.netloc.replace("-pooler.", ".")

    kept = [(k, v) for k, v in parse_qsl(parts.query) if k not in _LIBPQ_ONLY]
    return urlunsplit(
        ("postgresql+asyncpg", netloc, parts.path, urlencode(kept), parts.fragment)
    )


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,             # detect VARCHAR(50) -> VARCHAR(120)
        compare_server_default=True,
        render_as_batch=False,         # Postgres does not need batch mode
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=get_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    import ssl

    connectable = async_engine_from_config(
        {"sqlalchemy.url": get_migration_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # one-shot CLI: never hold a pool
        connect_args={
            "ssl": ssl.create_default_context(),
            "statement_cache_size": 0,
        },
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
