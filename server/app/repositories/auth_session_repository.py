"""Refresh-session database access. No business logic, no HTTP."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_session import AuthSession


class AuthSessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        refresh_token_hash: str,
        expires_at: dt.datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
        session = AuthSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            # Truncate rather than reject: a bizarre User-Agent header should
            # not be able to fail a legitimate login.
            user_agent=(user_agent or None) and user_agent[:512],
        )
        self.db.add(session)
        await self.db.flush()
        await self.db.refresh(session)
        return session

    async def find_by_token_hash(self, token_hash: str) -> AuthSession | None:
        """Look up by digest.

        Returns revoked and expired sessions too — the service needs to see
        them to distinguish "expired, log in again" from "reused after
        rotation, you have been robbed".
        """
        stmt = select(AuthSession).where(AuthSession.refresh_token_hash == token_hash)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_by_id(self, session_id: uuid.UUID) -> AuthSession | None:
        return await self.db.get(AuthSession, session_id)

    async def touch(self, session: AuthSession) -> AuthSession:
        session.last_used_at = dt.datetime.now(dt.UTC)
        await self.db.flush()
        return session

    async def revoke(
        self, session: AuthSession, *, rotated_to_id: uuid.UUID | None = None
    ) -> AuthSession:
        """Mark revoked, optionally recording the successor session.

        Passing rotated_to_id is what separates "replaced by rotation" from
        "revoked by logout" later.
        """
        if session.revoked_at is None:
            session.revoked_at = dt.datetime.now(dt.UTC)
        if rotated_to_id is not None:
            session.rotated_to_id = rotated_to_id
        await self.db.flush()
        return session

    async def claim_for_rotation(self, session_id: uuid.UUID) -> bool:
        """Atomically claim a live session for rotation. True if we won it.

        This is what makes rotation safe under concurrency, and it has to be a
        single conditional statement rather than a read-then-write.

        The read-then-write version has a real hole: four parallel refreshes
        presenting the same token each SELECT the row, all four see
        `revoked_at IS NULL`, and all four rotate. The result is four live
        sessions minted from one token — which is precisely the state rotation
        exists to make impossible, and it means a stolen token that is used
        alongside the real client is never detected.

        `UPDATE ... WHERE revoked_at IS NULL` pushes the decision into the
        database, where the row lock serialises it. Exactly one caller sees
        rowcount 1; the rest see 0 and are rejected.

        Deliberately NOT setting `rotated_to_id` here — the successor does not
        exist yet. The caller fills it in once the new session is created, so a
        loser of this race is left as a plain revoked row rather than one that
        claims a successor it never had.
        """
        stmt = (
            update(AuthSession)
            .where(
                AuthSession.id == session_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=dt.datetime.now(dt.UTC))
        )
        return int((await self.db.execute(stmt)).rowcount or 0) == 1

    async def link_rotation(
        self, session_id: uuid.UUID, *, rotated_to_id: uuid.UUID
    ) -> None:
        """Record the successor on an already-claimed session.

        Separate from the claim because the new session's id only exists after
        the claim has been won.
        """
        stmt = (
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(rotated_to_id=rotated_to_id)
        )
        await self.db.execute(stmt)

    async def revoke_all_for_user(
        self, user_id: uuid.UUID, *, exclude_session_id: uuid.UUID | None = None
    ) -> int:
        """Revoke every live session for a user. Returns how many were hit.

        Used by logout-all and by the reuse-detection path, where the safe
        response to a stolen token is to sign every device out.
        """
        stmt = (
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=dt.datetime.now(dt.UTC))
        )
        if exclude_session_id is not None:
            stmt = stmt.where(AuthSession.id != exclude_session_id)

        result = await self.db.execute(stmt)
        await self.db.flush()
        return int(result.rowcount or 0)

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[AuthSession]:
        now = dt.datetime.now(dt.UTC)
        stmt = (
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_active_for_user(self, user_id: uuid.UUID) -> int:
        now = dt.datetime.now(dt.UTC)
        stmt = (
            select(func.count())
            .select_from(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def delete_expired(self, *, older_than_days: int = 0) -> int:
        """Housekeeping for a scheduled job.

        Expired rows are kept briefly rather than deleted on expiry, because
        reuse detection needs the row to still exist to recognise a stolen
        token. Purge them once they can no longer tell you anything.
        """
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=older_than_days)
        stmt = select(AuthSession).where(AuthSession.expires_at < cutoff)
        rows = list((await self.db.execute(stmt)).scalars().all())
        for row in rows:
            await self.db.delete(row)
        await self.db.flush()
        return len(rows)
