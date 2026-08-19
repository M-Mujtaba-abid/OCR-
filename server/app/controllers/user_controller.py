"""User administration controller: HTTP in, HTTP out.

Shapes service results into the project's response envelope. No SQL, no rules.
"""

from __future__ import annotations

import math
import uuid

from app.lib.responses import ApiResponse, PaginatedData, PaginationMeta
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserRead, UserRoleUpdate, UserStats
from app.services.user_service import UserService


class UserController:
    def __init__(self, service: UserService) -> None:
        self.service = service

    async def list_users(
        self, *, actor: User, page: int, page_size: int, role: UserRole | None
    ) -> ApiResponse[PaginatedData[UserRead]]:
        items, total = await self.service.list_users(
            actor=actor, page=page, page_size=page_size, role=role
        )
        return ApiResponse.ok(
            PaginatedData[UserRead](
                items=[UserRead.model_validate(u) for u in items],
                pagination=PaginationMeta(
                    page=page,
                    page_size=page_size,
                    total=total,
                    # ceil, so 21 users at 20 per page is 2 pages, not 1.
                    pages=max(1, math.ceil(total / page_size)),
                ),
            ),
            message="Users retrieved",
        )

    async def get_stats(self, *, actor: User) -> ApiResponse[UserStats]:
        return ApiResponse.ok(
            await self.service.get_stats(actor=actor), message="Stats retrieved"
        )

    async def get_user(
        self, *, actor: User, user_id: uuid.UUID
    ) -> ApiResponse[UserRead]:
        user = await self.service.get_user(actor=actor, user_id=user_id)
        return ApiResponse.ok(UserRead.model_validate(user), message="User retrieved")

    async def create_user(
        self, *, actor: User, payload: UserCreate
    ) -> ApiResponse[UserRead]:
        user = await self.service.create_user(
            actor=actor,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            role=payload.role,
        )
        return ApiResponse.ok(
            UserRead.model_validate(user), message="User created"
        )

    async def change_role(
        self, *, actor: User, user_id: uuid.UUID, payload: UserRoleUpdate
    ) -> ApiResponse[UserRead]:
        user = await self.service.change_role(
            actor=actor, user_id=user_id, new_role=payload.role
        )
        return ApiResponse.ok(
            UserRead.model_validate(user),
            message=f"Role updated to {payload.role.value}",
        )

    async def set_active(
        self, *, actor: User, user_id: uuid.UUID, is_active: bool
    ) -> ApiResponse[UserRead]:
        user = await self.service.set_active(
            actor=actor, user_id=user_id, is_active=is_active
        )
        return ApiResponse.ok(
            UserRead.model_validate(user),
            message="Account enabled" if is_active else "Account disabled",
        )
