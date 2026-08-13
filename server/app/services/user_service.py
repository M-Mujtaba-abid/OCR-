"""User administration business rules.

Everything here is a rule that must hold regardless of who is calling or over
what protocol, which is why it lives in the service and not in the controller:
an admin CLI or a background job would need exactly the same guarantees.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, UserNotFoundError
from app.lib.logging import get_logger
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserStats

logger = get_logger(__name__)


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)

    async def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        role: UserRole | None = None,
    ) -> tuple[list[User], int]:
        """Return one page of users plus the unpaginated total."""
        offset = (page - 1) * page_size
        items = await self.users.list_users(limit=page_size, offset=offset, role=role)
        total = await self.users.count(role=role)
        return items, total

    async def get_stats(self) -> UserStats:
        by_role = await self.users.count_by_role()
        active, verified = await self.users.count_flags()
        total = sum(by_role.values())

        return UserStats(
            total=total,
            active=active,
            # Derived rather than queried: a third count would have to be taken
            # in the same transaction to stay consistent with the first two.
            inactive=total - active,
            verified=verified,
            # Zero-fill so the dashboard renders every role even before anyone
            # holds it, instead of the card silently disappearing.
            by_role={role: by_role.get(role, 0) for role in UserRole},
        )

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.users.find_by_id(user_id)
        if user is None:
            raise UserNotFoundError()
        return user

    async def change_role(
        self, *, actor: User, user_id: uuid.UUID, new_role: UserRole
    ) -> User:
        """Promote or demote a user.

        Three guards, each closing a way an administrator can lock everyone out
        of the system:

          1. **No self-service.** An admin cannot change their own role. Without
             this, a mis-click demotes the person holding the only admin account
             and there is no longer anyone who can undo it.
          2. **Never remove the last admin.** Same failure, reached by demoting
             someone else instead of yourself.
          3. **No-op short circuit.** Setting the role a user already has does
             not write, so the audit log is not polluted with empty changes.

        The route's `require_permission("user.update")` has already established
        that the caller may do this at all; these are the rules about *what* is
        a legal change, which is a different question.
        """
        target = await self.get_user(user_id)

        if target.id == actor.id:
            raise ForbiddenError(
                "You cannot change your own role.",
                code="CANNOT_MODIFY_SELF",
            )

        if target.role == new_role:
            return target

        if target.role is UserRole.ADMIN and new_role is not UserRole.ADMIN:
            admin_count = await self.users.count(role=UserRole.ADMIN)
            if admin_count <= 1:
                raise ConflictError(
                    "Cannot demote the last administrator.",
                    code="LAST_ADMIN",
                )

        previous = target.role
        await self.users.update(target, role=new_role)
        await self.db.commit()
        await self.db.refresh(target)

        # A privilege change is exactly the sort of event that must be
        # reconstructible after the fact. Ids only — no emails in the log.
        logger.info(
            "Role changed: user=%s %s -> %s by actor=%s",
            target.id,
            previous.value,
            new_role.value,
            actor.id,
        )
        return target

    async def set_active(
        self, *, actor: User, user_id: uuid.UUID, is_active: bool
    ) -> User:
        """Enable or disable an account.

        Disabling takes effect on the very next request, not when the access
        token expires, because `get_current_user` reloads the user from the
        database each time. Existing refresh tokens are left in place
        deliberately — re-enabling the account should restore it, not force
        every device to sign in again.
        """
        target = await self.get_user(user_id)

        if target.id == actor.id:
            raise ForbiddenError(
                "You cannot disable your own account.",
                code="CANNOT_MODIFY_SELF",
            )

        if target.is_active == is_active:
            return target

        if target.role is UserRole.ADMIN and not is_active:
            active_admins = [
                u
                for u in await self.users.list_users(limit=1000, role=UserRole.ADMIN)
                if u.is_active
            ]
            if len(active_admins) <= 1:
                raise ConflictError(
                    "Cannot disable the last active administrator.",
                    code="LAST_ADMIN",
                )

        await self.users.update(target, is_active=is_active)
        await self.db.commit()
        await self.db.refresh(target)

        logger.info(
            "Account %s: user=%s by actor=%s",
            "enabled" if is_active else "disabled",
            target.id,
            actor.id,
        )
        return target
