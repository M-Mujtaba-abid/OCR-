"""User database access. No business logic, no HTTP."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def normalize_email(email: str) -> str:
        """Single definition of how an email becomes a lookup key.

        Emails are case-insensitive in practice, so storing and querying the
        lowercased form lets a plain unique index enforce "one account per
        address" — no functional index required.
        """
        return email.strip().lower()

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        full_name: str | None = None,
        role: UserRole = UserRole.MEMBER,
        is_active: bool = True,
        is_verified: bool = False,
    ) -> User:
        """Insert and flush — but do NOT commit.

        Flushing assigns the primary key so the caller can use it immediately,
        while leaving the transaction open so the service can decide whether
        the whole unit of work succeeds.
        """
        user = User(
            email=self.normalize_email(email),
            password_hash=password_hash,
            full_name=full_name,
            role=role,
            is_active=is_active,
            is_verified=is_verified,
        )
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def find_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.db.get(User, user_id)

    async def find_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == self.normalize_email(email))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        stmt = select(func.count()).select_from(User).where(
            User.email == self.normalize_email(email)
        )
        return bool((await self.db.execute(stmt)).scalar_one())

    async def update(self, user: User, **fields: object) -> User:
        for key, value in fields.items():
            if not hasattr(user, key):
                raise AttributeError(f"User has no field {key!r}")
            setattr(user, key, value)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def set_password_hash(self, user: User, password_hash: str) -> User:
        return await self.update(user, password_hash=password_hash)

    async def delete(self, user: User) -> None:
        await self.db.delete(user)
        await self.db.flush()

    async def list_users(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        role: UserRole | None = None,
    ) -> list[User]:
        stmt = select(User).order_by(User.created_at.desc())
        if role is not None:
            stmt = stmt.where(User.role == role)
        return list(
            (await self.db.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )

    async def count(self, *, role: UserRole | None = None) -> int:
        stmt = select(func.count()).select_from(User)
        if role is not None:
            stmt = stmt.where(User.role == role)
        return int((await self.db.execute(stmt)).scalar_one())

    async def count_by_role(self) -> dict[UserRole, int]:
        """One GROUP BY rather than three COUNTs.

        Roles with no users are absent from the result — the caller fills the
        gaps, because the repository should report what the database contains,
        not what the enum happens to define.
        """
        stmt = select(User.role, func.count()).group_by(User.role)
        return {role: int(total) for role, total in (await self.db.execute(stmt)).all()}

    async def count_flags(self) -> tuple[int, int]:
        """(active, verified) in a single round trip.

        FILTER is the Postgres way to do conditional aggregation; SUM(CASE...)
        would work too but reads worse and returns NULL on an empty table.
        """
        stmt = select(
            func.count().filter(User.is_active.is_(True)),
            func.count().filter(User.is_verified.is_(True)),
        ).select_from(User)
        active, verified = (await self.db.execute(stmt)).one()
        return int(active or 0), int(verified or 0)
