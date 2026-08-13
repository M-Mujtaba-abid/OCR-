"""Auth routes — HTTP surface only.

Each function declares its method, path, dependencies and response model, then
delegates to the controller. No business logic, no SQL, no token handling.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.auth_controller import AuthController
from app.db.session import get_db
from app.dependencies.auth import (
    CurrentActiveUser,
    get_client_ip,
    get_user_agent,
)
from app.lib.responses import ApiErrorResponse, ApiResponse
from app.schemas.auth import (
    LoginData,
    LoginRequest,
    LogoutData,
    RegisterRequest,
    SessionRead,
    TokenData,
)
from app.schemas.user import UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_controller(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthController:
    """Wire the layer chain: controller -> service -> repositories -> db."""
    return AuthController(AuthService(db))


Controller = Annotated[AuthController, Depends(get_auth_controller)]
ClientIp = Annotated[str | None, Depends(get_client_ip)]
UserAgent = Annotated[str | None, Depends(get_user_agent)]

# Documents the error envelope in OpenAPI so consumers see the real shape.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ApiErrorResponse},
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
}


@router.post(
    "/register",
    response_model=ApiResponse[UserRead],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
    responses=ERROR_RESPONSES,
)
async def register(
    payload: RegisterRequest, controller: Controller
) -> ApiResponse[UserRead]:
    return await controller.register(payload)


@router.post(
    "/login",
    response_model=ApiResponse[LoginData],
    summary="Log in and receive an access token",
    responses=ERROR_RESPONSES,
)
async def login(
    payload: LoginRequest,
    response: Response,
    controller: Controller,
    ip_address: ClientIp,
    user_agent: UserAgent,
) -> ApiResponse[LoginData]:
    return await controller.login(
        payload, response, ip_address=ip_address, user_agent=user_agent
    )


@router.post(
    "/refresh",
    response_model=ApiResponse[TokenData],
    summary="Rotate the refresh token and issue a new access token",
    responses=ERROR_RESPONSES,
)
async def refresh(
    request: Request,
    response: Response,
    controller: Controller,
    ip_address: ClientIp,
    user_agent: UserAgent,
) -> ApiResponse[TokenData]:
    # No auth dependency: the access token is expected to be expired here.
    # The refresh cookie is the credential, read inside the controller.
    return await controller.refresh(
        request, response, ip_address=ip_address, user_agent=user_agent
    )


@router.post(
    "/logout",
    response_model=ApiResponse[LogoutData],
    summary="Revoke the current session",
    responses=ERROR_RESPONSES,
)
async def logout(
    request: Request, response: Response, controller: Controller
) -> ApiResponse[LogoutData]:
    # Intentionally unauthenticated: a client with an expired access token must
    # still be able to log out and clear its cookie.
    return await controller.logout(request, response)


@router.post(
    "/logout-all",
    response_model=ApiResponse[LogoutData],
    summary="Revoke every session for the current user",
    responses=ERROR_RESPONSES,
)
async def logout_all(
    user: CurrentActiveUser, response: Response, controller: Controller
) -> ApiResponse[LogoutData]:
    return await controller.logout_all(user, response)


@router.get(
    "/me",
    response_model=ApiResponse[UserRead],
    summary="Get the authenticated user",
    responses=ERROR_RESPONSES,
)
async def me(user: CurrentActiveUser, controller: Controller) -> ApiResponse[UserRead]:
    return await controller.me(user)


@router.get(
    "/sessions",
    response_model=ApiResponse[list[SessionRead]],
    summary="List active sessions for the current user",
    responses=ERROR_RESPONSES,
)
async def sessions(
    user: CurrentActiveUser, controller: Controller
) -> ApiResponse[list[SessionRead]]:
    return await controller.sessions(user)
