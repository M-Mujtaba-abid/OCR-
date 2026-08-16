"""Authentication business logic.

Knows nothing about HTTP: no Request, no Response, no cookies, no status codes.
It receives plain values, returns plain values, and raises AppError subclasses.
Cookie handling lives in the controller, where it belongs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    EmailAlreadyRegisteredError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReusedError,
)
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_and_update_password,
    verify_password,
)
from app.lib.logging import get_logger
from app.models.auth_session import AuthSession
from app.models.user import User, UserRole
from app.repositories.auth_session_repository import AuthSessionRepository
from app.repositories.user_repository import UserRepository

logger = get_logger(__name__)


@dataclass(slots=True)
class IssuedTokens:
    """What the service hands back after login or refresh.

    `refresh_token` is the raw value and exists only in memory, on this object,
    for the moment it takes the controller to write it into a Set-Cookie
    header. It is never persisted, logged, or returned in a response body.
    """

    access_token: str
    access_expires_at: dt.datetime
    refresh_token: str
    refresh_expires_at: dt.datetime
    session_id: uuid.UUID
    user: User

    @property
    def expires_in(self) -> int:
        delta = self.access_expires_at - dt.datetime.now(dt.UTC)
        return max(int(delta.total_seconds()), 0)


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.sessions = AuthSessionRepository(db)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    async def register(
        self, *, email: str, password: str, full_name: str | None = None
    ) -> User:
        if await self.users.email_exists(email):
            raise EmailAlreadyRegisteredError()

        user = await self.users.create(
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            # Always the lowest role. Never derive privilege from user input —
            # an attacker-supplied `role` field is how self-registration
            # becomes privilege escalation.
            role=UserRole.MEMBER,
            is_active=True,
            is_verified=False,
        )
        await self.db.commit()
        logger.info("user registered: %s", user.id)
        return user

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    async def login(
        self,
        *,
        email: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedTokens:
        user = await self.users.find_by_email(email)

        if user is None:
            # Hash a dummy value anyway so an unknown email costs the same time
            # as a known one. Skipping this leaks which addresses are
            # registered through response timing alone.
            verify_password(password, DUMMY_PASSWORD_HASH)
            raise InvalidCredentialsError()

        valid, updated_hash = verify_and_update_password(password, user.password_hash)
        if not valid:
            raise InvalidCredentialsError()

        # Argon2's recommended parameters rise over time. When they do, this
        # silently upgrades the stored hash on the user's next login.
        if updated_hash:
            await self.users.set_password_hash(user, updated_hash)

        # Checked AFTER the password, so a disabled account cannot be used as
        # an oracle to confirm an email exists.
        if not user.is_active:
            raise InactiveUserError()

        tokens = await self._issue_tokens(
            user, ip_address=ip_address, user_agent=user_agent
        )
        await self.db.commit()
        logger.info("login succeeded: user=%s session=%s", user.id, tokens.session_id)
        return tokens

    # ------------------------------------------------------------------
    # Refresh with rotation
    # ------------------------------------------------------------------
    async def refresh(
        self,
        *,
        raw_refresh_token: str | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedTokens:
        if not raw_refresh_token:
            raise InvalidRefreshTokenError("No refresh token supplied.")

        token_hash = hash_refresh_token(raw_refresh_token)
        session = await self.sessions.find_by_token_hash(token_hash)

        if session is None:
            raise InvalidRefreshTokenError()

        # --- theft detection -------------------------------------------------
        # A revoked session that was rotated means this token already had a
        # successor issued. The legitimate client would be holding that
        # successor, so whoever presented this one copied it. The safe response
        # is to sign every device out and force a fresh login.
        #
        # Except immediately after the rotation. A client that fired two
        # refreshes at once — a retry, a double-click, two tabs waking together
        # — has the loser arrive moments after the winner committed, and it
        # looks identical to a replay from here. Treating that as theft signs
        # an honest user out of every device for a double-click.
        #
        # Inside the grace window the loser is simply refused. It gets no
        # session either way: the successor has already been issued to the
        # request that won.
        if session.revoked_at is not None and session.was_rotated:
            age = (dt.datetime.now(dt.UTC) - session.revoked_at).total_seconds()
            if age <= settings.REFRESH_REUSE_GRACE_SECONDS:
                logger.info(
                    "refresh presented %.1fs after its own rotation — treating as a "
                    "duplicate, not theft: user=%s session=%s",
                    age,
                    session.user_id,
                    session.id,
                )
                raise InvalidRefreshTokenError("This token has already been used.")

            revoked = await self.sessions.revoke_all_for_user(session.user_id)
            await self.db.commit()
            logger.warning(
                "refresh token reuse detected: user=%s session=%s; revoked %d sessions",
                session.user_id,
                session.id,
                revoked,
            )
            raise RefreshTokenReusedError()

        if session.revoked_at is not None:
            raise InvalidRefreshTokenError("Session has been revoked.")

        if session.expires_at <= dt.datetime.now(dt.UTC):
            raise InvalidRefreshTokenError("Session has expired.")

        user = await self.users.find_by_id(session.user_id)
        if user is None:
            raise InvalidRefreshTokenError()
        if not user.is_active:
            raise InactiveUserError()

        # --- rotate ----------------------------------------------------------
        # Claim the old session FIRST, with a conditional UPDATE, and only mint
        # a successor if we won it.
        #
        # The obvious ordering — create the new session, then revoke the old —
        # is what made this racy. Four parallel refreshes with the same token
        # all passed the checks above, all created a session, and all revoked
        # the same row: four live sessions from one token, and rotation
        # providing no protection at all. Verified, not theoretical.
        #
        # `claim_for_rotation` pushes the decision into a single statement the
        # database serialises, so exactly one caller proceeds.
        # Read off the ORM object BEFORE any rollback. A rollback expires every
        # attribute, and reading one afterwards triggers a lazy refresh — which
        # inside async SQLAlchemy raises MissingGreenlet and turns a clean 401
        # into a 503.
        session_id, session_user_id = session.id, session.user_id

        if not await self.sessions.claim_for_rotation(session_id):
            # Someone else rotated this token between our SELECT and here. The
            # legitimate client holds the successor, so this request is either
            # a duplicate in flight or a replay. Either way it must not get a
            # session — and it is not treated as theft, because a client that
            # simply fired two refreshes at once has done nothing wrong.
            await self.db.rollback()
            logger.info(
                "refresh lost the rotation race: user=%s session=%s",
                session_user_id,
                session_id,
            )
            raise InvalidRefreshTokenError("This token has already been used.")

        new_tokens = await self._issue_tokens(
            user, ip_address=ip_address, user_agent=user_agent
        )
        # Recorded after the successor exists, so `rotated_to_id` never points
        # at a row that was never created.
        await self.sessions.link_rotation(
            session_id, rotated_to_id=new_tokens.session_id
        )
        await self.db.commit()
        logger.info(
            "refresh rotated: user=%s old=%s new=%s",
            new_tokens.user.id,
            session_id,
            new_tokens.session_id,
        )
        return new_tokens

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------
    async def logout(self, *, raw_refresh_token: str | None) -> int:
        """Revoke the session behind this refresh token.

        Idempotent and silent on an unknown token: logout must never report
        whether a token was real, and a client clearing a stale cookie should
        not receive an error.
        """
        if not raw_refresh_token:
            return 0

        session = await self.sessions.find_by_token_hash(
            hash_refresh_token(raw_refresh_token)
        )
        if session is None or session.revoked_at is not None:
            return 0

        await self.sessions.revoke(session)
        await self.db.commit()
        logger.info("logout: session=%s", session.id)
        return 1

    async def logout_all(self, *, user_id: uuid.UUID) -> int:
        count = await self.sessions.revoke_all_for_user(user_id)
        await self.db.commit()
        logger.info("logout-all: user=%s sessions=%d", user_id, count)
        return count

    async def list_sessions(self, *, user_id: uuid.UUID) -> list[AuthSession]:
        return await self.sessions.list_active_for_user(user_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _issue_tokens(
        self,
        user: User,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> IssuedTokens:
        access_token, access_expires_at = create_access_token(user.id)

        raw_refresh = generate_refresh_token()
        refresh_expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )

        session = await self.sessions.create(
            user_id=user.id,
            # Only the digest is persisted; raw_refresh never touches the DB.
            refresh_token_hash=hash_refresh_token(raw_refresh),
            expires_at=refresh_expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return IssuedTokens(
            access_token=access_token,
            access_expires_at=access_expires_at,
            refresh_token=raw_refresh,
            refresh_expires_at=refresh_expires_at,
            session_id=session.id,
            user=user,
        )
