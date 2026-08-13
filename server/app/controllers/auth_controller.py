"""Auth controller: HTTP in, HTTP out.

Coordinates requests and responses. Contains no SQL and no authentication
rules — it calls the service for those. It DOES own cookie handling, because a
Set-Cookie header is an HTTP concern and putting it in the service would drag
the framework into the business layer.
"""

from __future__ import annotations

import datetime as dt

from fastapi import Request, Response

from app.core.config import settings
from app.dependencies.auth import user_permissions
from app.lib.responses import ApiResponse
from app.models.user import User
from app.schemas.auth import (
    LoginData,
    LoginRequest,
    LogoutData,
    RegisterRequest,
    SessionRead,
    TokenData,
)
from app.schemas.user import UserRead
from app.services.auth_service import AuthService, IssuedTokens


class AuthController:
    def __init__(self, service: AuthService) -> None:
        self.service = service

    # ------------------------------------------------------------------
    # Cookie helpers
    # ------------------------------------------------------------------
    def _set_refresh_cookie(self, response: Response, tokens: IssuedTokens) -> None:
        """Write the refresh token as an HttpOnly cookie.

        httponly    — JavaScript cannot read it, so XSS cannot exfiltrate it.
                      This is the whole reason it is not in the response body.
        secure      — HTTPS only. Must be true in production; config enforces it.
        samesite    — 'lax' blocks the cross-site POSTs that drive CSRF.
        path        — scoped to /api/v1/auth so it is not attached to every
                      ordinary API call.
        max_age     — matches the session's real database expiry, so the
                      browser stops sending a token the server would reject.
        """
        # Derived from the session's real expiry rather than recomputed from
        # config, so the cookie and the database row can never disagree.
        # Max-Age only, no Expires: Starlette serialises an int Expires as a
        # raw number, which is not a valid HTTP date, and Max-Age takes
        # precedence in every browser anyway.
        max_age = max(
            int((tokens.refresh_expires_at - dt.datetime.now(dt.UTC)).total_seconds()),
            0,
        )
        response.set_cookie(
            key=settings.AUTH_COOKIE_NAME,
            value=tokens.refresh_token,
            max_age=max_age,
            path=settings.AUTH_COOKIE_PATH,
            domain=settings.AUTH_COOKIE_DOMAIN,
            secure=settings.AUTH_COOKIE_SECURE,
            httponly=True,
            samesite=settings.AUTH_COOKIE_SAMESITE,
        )

    def _clear_refresh_cookie(self, response: Response) -> None:
        # Attributes must match the ones used when setting it, or the browser
        # treats it as a different cookie and quietly keeps the original.
        response.delete_cookie(
            key=settings.AUTH_COOKIE_NAME,
            path=settings.AUTH_COOKIE_PATH,
            domain=settings.AUTH_COOKIE_DOMAIN,
            secure=settings.AUTH_COOKIE_SECURE,
            httponly=True,
            samesite=settings.AUTH_COOKIE_SAMESITE,
        )

    @staticmethod
    def _read_refresh_cookie(request: Request) -> str | None:
        return request.cookies.get(settings.AUTH_COOKIE_NAME)

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    async def register(self, payload: RegisterRequest) -> ApiResponse[UserRead]:
        user = await self.service.register(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
        return ApiResponse.ok(
            data=UserRead.model_validate(user),
            message="Account created successfully",
        )

    async def login(
        self,
        payload: LoginRequest,
        response: Response,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> ApiResponse[LoginData]:
        tokens = await self.service.login(
            email=payload.email,
            password=payload.password,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._set_refresh_cookie(response, tokens)
        return ApiResponse.ok(
            data=LoginData(
                access_token=tokens.access_token,
                expires_in=tokens.expires_in,
                expires_at=tokens.access_expires_at,
                user=UserRead.model_validate(tokens.user),
            ),
            message="Login successful",
        )

    async def refresh(
        self,
        request: Request,
        response: Response,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> ApiResponse[TokenData]:
        tokens = await self.service.refresh(
            raw_refresh_token=self._read_refresh_cookie(request),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._set_refresh_cookie(response, tokens)
        return ApiResponse.ok(
            data=TokenData(
                access_token=tokens.access_token,
                expires_in=tokens.expires_in,
                expires_at=tokens.access_expires_at,
            ),
            message="Token refreshed",
        )

    async def logout(
        self, request: Request, response: Response
    ) -> ApiResponse[LogoutData]:
        count = await self.service.logout(
            raw_refresh_token=self._read_refresh_cookie(request)
        )
        # Cleared unconditionally: even if the token was already unknown, the
        # client should end up without one.
        self._clear_refresh_cookie(response)
        return ApiResponse.ok(
            data=LogoutData(revoked_sessions=count), message="Logged out"
        )

    async def logout_all(
        self, user: User, response: Response
    ) -> ApiResponse[LogoutData]:
        count = await self.service.logout_all(user_id=user.id)
        self._clear_refresh_cookie(response)
        return ApiResponse.ok(
            data=LogoutData(revoked_sessions=count),
            message="Logged out from all devices",
        )

    async def me(self, user: User) -> ApiResponse[UserRead]:
        return ApiResponse.ok(
            data=UserRead.model_validate(user), message="Current user fetched"
        )

    async def permissions(self, user: User) -> ApiResponse[list[str]]:
        """The caller's effective permissions.

        Exists so the frontend can decide what to *show* without hard-coding a
        second copy of ROLE_PERMISSIONS that silently drifts from this one.
        It is a UX aid and nothing more — the frontend is told what it may see,
        never trusted about what it may do. Every route still enforces its own
        permission server-side.
        """
        return ApiResponse.ok(
            data=sorted(user_permissions(user)), message="Permissions fetched"
        )

    async def sessions(self, user: User) -> ApiResponse[list[SessionRead]]:
        rows = await self.service.list_sessions(user_id=user.id)
        return ApiResponse.ok(
            data=[SessionRead.model_validate(r) for r in rows],
            message="Active sessions fetched",
        )
