"""Async database engine and the FastAPI session dependency."""

from __future__ import annotations

import os
import ssl
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# Neon requires TLS and presents a publicly-trusted certificate, so the default
# context (which verifies hostname + chain) works and is safer than ssl=True.
_ssl_context = ssl.create_default_context()

# Neon's pooled endpoint is PgBouncer in transaction mode. It hands the same
# server connection to different clients between transactions, so asyncpg's
# per-connection prepared-statement cache goes stale and errors with
# "prepared statement __asyncpg_stmt_x__ does not exist". Disabling both caches
# is the fix. On a direct (non-pooler) endpoint this is merely a small cost.
_statement_cache_size = 0 if settings.is_pooled else 100

# On a serverless platform every warm instance keeps its OWN pool, so the
# settings that are right for one long-lived process become a connection storm
# across dozens of short-lived ones — five each, plus overflow, against a
# database that caps them. NullPool hands the pooling job to the one process
# equipped to do it: Neon's own pooler, which the DSN already points at.
#
# `VERCEL` is set by the platform, so this needs no configuration of its own
# and cannot be wrong locally.
_serverless = bool(os.getenv("VERCEL"))
_pool_options: dict[str, Any] = (
    {"poolclass": NullPool}
    if _serverless
    else {"pool_size": 5, "max_overflow": 10, "pool_recycle": 300}
)

engine: AsyncEngine = create_async_engine(
    settings.async_dsn,
    echo=settings.DEBUG,
    # Off by default, and measured rather than assumed: against this Neon
    # instance a pre-ping cost ~1.9 SECONDS per request (4,797 ms vs 2,877 ms
    # for the same three queries). It is not the ping itself — the ping finds
    # an idle connection Neon has dropped and transparently rebuilds it, TLS
    # handshake and all, on requests that would otherwise have been fine.
    #
    # `pool_recycle` below is what replaces it: SQLAlchemy checks a connection's
    # AGE at checkout and discards an old one without touching the network, so
    # the dead-connection case is handled locally instead of at 500 ms a time.
    # Set DB_POOL_PRE_PING=true to put it back without a code change.
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    # pool_size / max_overflow / pool_recycle, or NullPool under serverless.
    **_pool_options,
    # NOTE: the dialect-level `prepared_statement_cache_size` is set as a URL
    # param in core/config.py — create_async_engine() rejects it as a kwarg.
    connect_args={
        "ssl": _ssl_context,
        "statement_cache_size": _statement_cache_size,
        "server_settings": {"application_name": settings.PROJECT_NAME},
    },
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # Lets you read model attributes after commit, while the response is being
    # serialized, without a re-SELECT on a closed session (MissingGreenlet).
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session.

    Contract: the *caller* commits. This guarantees rollback on any escaping
    exception and always returns the connection to the pool.

        @router.get("/things")
        async def list_things(db: AsyncSession = Depends(get_db)): ...
    """
    session = SessionFactory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
