"""User database access. No business logic, no HTTP."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

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
        company_id: uuid.UUID | None,
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

        `company_id` is required and has NO default, on purpose. A default here
        would be a guess about which business a new person works for, made in
        the one place that cannot possibly know. Passing None is legal for the
        platform owner alone, and the database's check constraint is what holds
        anybody else to it.
        """
        user = User(
            company_id=company_id,
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
        """One user, with their company already loaded.

        The company rides along on every lookup because authentication needs
        it: a suspended company has to stop its members on the very next
        request, and that check cannot be a second round trip on the hot path
        of every authenticated call. It is a one-row join to a tiny table.

        A `select` rather than `db.get`, deliberately. `db.get` consults the
        identity map first and returns a cached instance WITHOUT applying the
        loader options — so a user already in the session comes back with
        `company` unloaded, and reading it raises under `lazy="raise"`. The
        primary-key lookup this emits is the same one `db.get` would have.
        """
        stmt = (
            select(User).where(User.id == user_id).options(joinedload(User.company))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

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
        company_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        role: UserRole | None = None,
    ) -> list[User]:
        """One company's directory. Never everybody's.

        `company_id` is required and undefaulted throughout this repository.
        A default would make "every user in the system" the behaviour a caller
        gets by forgetting, and forgetting is the failure mode this whole
        boundary exists to survive.
        """
        stmt = (
            select(User)
            .where(User.company_id == company_id)
            .order_by(User.created_at.desc())
        )
        if role is not None:
            stmt = stmt.where(User.role == role)
        return list(
            (await self.db.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )

    async def count(
        self, *, company_id: uuid.UUID, role: UserRole | None = None
    ) -> int:
        """How many users this company has.

        The "last administrator" guards are built on this, so an unscoped count
        would let a company demote its only admin as long as SOME other company
        still had one — locking them out of their own account management.
        """
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.company_id == company_id)
        )
        if role is not None:
            stmt = stmt.where(User.role == role)
        return int((await self.db.execute(stmt)).scalar_one())

    async def list_ids_by_role(
        self, *roles: UserRole, company_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Ids only — used to fan a notification out to every admin.

        Selecting the column rather than the entity avoids hydrating full User
        objects that are immediately discarded.

        `company_id` is keyword-only and has no default, because the one thing
        this must never do is answer "every admin" when it was asked "every
        admin here". Without it, one company's failed extraction is announced
        to another company's administrators, along with the file name.
        """
        stmt = select(User.id).where(
            User.role.in_(roles),
            User.is_active.is_(True),
            User.company_id == company_id,
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_by_role(self, *, company_id: uuid.UUID) -> dict[UserRole, int]:
        """One GROUP BY rather than three COUNTs.

        Roles with no users are absent from the result — the caller fills the
        gaps, because the repository should report what the database contains,
        not what the enum happens to define.
        """
        stmt = (
            select(User.role, func.count())
            .where(User.company_id == company_id)
            .group_by(User.role)
        )
        return {role: int(total) for role, total in (await self.db.execute(stmt)).all()}

    async def count_flags(self, *, company_id: uuid.UUID) -> tuple[int, int]:
        """(active, verified) in a single round trip.

        FILTER is the Postgres way to do conditional aggregation; SUM(CASE...)
        would work too but reads worse and returns NULL on an empty table.
        """
        stmt = (
            select(
                func.count().filter(User.is_active.is_(True)),
                func.count().filter(User.is_verified.is_(True)),
            )
            .select_from(User)
            .where(User.company_id == company_id)
        )
        active, verified = (await self.db.execute(stmt)).one()
        return int(active or 0), int(verified or 0)
