"""Test fixtures.

Isolation strategy: each test runs inside an outer transaction that is rolled
back at the end. The session is bound to that connection with
`join_transaction_mode="create_savepoint"`, so the `await db.commit()` calls
inside AuthService become SAVEPOINT releases rather than real commits. Tests
therefore exercise the genuine commit path while leaving the database
untouched — no cleanup code, no cross-test bleed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.core.security import hash_password


@pytest_asyncio.fixture
async def connection() -> AsyncGenerator[AsyncConnection]:
    conn = await engine.connect()
    transaction = await conn.begin()
    try:
        yield conn
    finally:
        # Undoes everything the test did, including "committed" work.
        if transaction.is_active:
            await transaction.rollback()
        await conn.close()


@pytest_asyncio.fixture
async def db(connection: AsyncConnection) -> AsyncGenerator[AsyncSession]:
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """HTTP client wired to the same rolled-back session the test sees."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def password() -> str:
    return "CorrectHorseBattery1"


@pytest_asyncio.fixture
async def existing_user(db: AsyncSession, unique_email: str, password: str) -> User:
    user = await UserRepository(db).create(
        email=unique_email,
        password_hash=hash_password(password),
        full_name="Existing User",
        role=UserRole.MEMBER,
    )
    await db.commit()
    return user


@pytest_asyncio.fixture
async def inactive_user(db: AsyncSession, password: str) -> User:
    user = await UserRepository(db).create(
        email=f"inactive-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        is_active=False,
    )
    await db.commit()
    return user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def login(client: AsyncClient, email: str, password: str):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


def auth_header(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
