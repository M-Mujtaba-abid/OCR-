"""Async database engine and the FastAPI session dependency."""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator

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

engine: AsyncEngine = create_async_engine(
    settings.async_dsn,
    echo=settings.DEBUG,
    pool_pre_ping=True,      # Neon scales to zero; this discards dead connections
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,        # recycle before Neon's idle timeout closes them
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
