"""FastAPI application entry point.

Run with:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.handlers import register_exception_handlers
from app.db.session import engine, get_db
from app.lib.logging import configure_logging, get_logger
from app.lib.responses import ApiResponse
from app.middleware.request_context import RequestContextMiddleware
from app.routes import api_router

logger = get_logger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    configure_logging(level="DEBUG" if settings.DEBUG else "INFO", debug_sql=False)

    # Ping the database at startup so a bad DSN fails loudly here rather than
    # 500-ing the first real request.
    async with engine.begin() as conn:
        version = (await conn.execute(text("SELECT version()"))).scalar_one()
    logger.info("connected to %s", settings.db_host)
    logger.info("%s", version.split(" on ")[0])
    logger.info("environment=%s debug=%s", settings.ENVIRONMENT, settings.DEBUG)

    yield

    # Must run, or asyncpg connections leak on every --reload cycle.
    await engine.dispose()
    logger.info("connection pool disposed")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
    )

    # Middleware runs in reverse registration order, so CORS is added last to
    # run first. That is what keeps CORS headers on error responses — without
    # it a 500 reaches the browser as an opaque CORS failure and you debug the
    # wrong problem entirely.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        # Required for the refresh cookie to be sent cross-origin.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
    )

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # ---- unversioned utility endpoints (preserved from the original app) ----
    @app.get("/hello", tags=["demo"], response_model=ApiResponse[dict[str, str]])
    async def hello() -> ApiResponse[dict[str, str]]:
        return ApiResponse.ok(
            data={"greeting": "Hello from FastAPI!"}, message="Hello endpoint"
        )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """Liveness — is the process up? Does not touch the database.

        Left outside the envelope on purpose: load balancers and container
        orchestrators expect a flat, minimal body here.
        """
        return {"status": "ok", "environment": settings.ENVIRONMENT}

    @app.get("/health/db", tags=["health"])
    async def health_db(db: DbSession) -> dict[str, object]:
        """Readiness — proves the pool, TLS and credentials actually work.
        /health would keep returning ok with a completely broken database."""
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

    return app


app = create_app()
