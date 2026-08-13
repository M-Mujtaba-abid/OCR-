"""FastAPI application entry point.

Run with:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import engine, get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Ping the database at startup so a bad DSN fails loudly here rather than
    # 500-ing the first real request.
    async with engine.begin() as conn:
        version = (await conn.execute(text("SELECT version()"))).scalar_one()
    print(f"[startup] connected to {settings.db_host}")
    print(f"[startup] {version}")

    yield

    # Must run, or asyncpg connections leak on every --reload cycle.
    await engine.dispose()
    print("[shutdown] connection pool disposed")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/hello", tags=["demo"])
async def hello() -> dict[str, str]:
    """The endpoint you asked for."""
    return {"message": "Hello from FastAPI!"}


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness — is the process up? Does not touch the database."""
    return {"status": "ok", "environment": settings.ENVIRONMENT}


@app.get("/health/db", tags=["health"])
async def health_db(db: DbSession) -> dict[str, object]:
    """Readiness — proves the pool, TLS and credentials all actually work.

    This is the endpoint that tells you the Neon connection is real; /health
    would keep returning ok with a completely broken database.
    """
    result = await db.execute(
        text("SELECT current_database(), current_user, version()")
    )
    database, user, version = result.one()
    return {
        "status": "connected",
        "database": database,
        "user": user,
        "host": settings.db_host,
        "pooled": settings.is_pooled,
        "server_version": version.split(" on ")[0],
    }
