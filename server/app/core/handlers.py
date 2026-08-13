"""Global exception handlers — the Express `globalErrorHandler` equivalent.

Registered once in the application factory. Every failure leaves the API in the
same envelope shape, so the frontend needs exactly one error parser.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import AppError
from app.lib.logging import get_logger

logger = get_logger(__name__)


def _envelope(
    *,
    message: str,
    code: str,
    request: Request,
    details: object | None = None,
) -> dict[str, object]:
    return {
        "success": False,
        "message": message,
        "error": {"code": code, "details": details},
        "request_id": getattr(request.state, "request_id", None),
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """Expected, deliberate failures. Logged at warning, never with a
        stack trace — these are control flow, not defects."""
        logger.warning(
            "%s %s -> %s (%s)",
            request.method,
            request.url.path,
            exc.status_code,
            exc.code,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                message=exc.message,
                code=exc.code,
                request=request,
                details=exc.details,
            ),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Pydantic rejection, reshaped into field -> message.

        FastAPI's raw `loc` arrays are awkward for a frontend to consume;
        flattening them here means the client can bind errors straight to form
        fields.
        """
        field_errors: dict[str, str] = {}
        for err in exc.errors():
            location = [str(p) for p in err["loc"] if p not in ("body", "query", "path")]
            field_errors[".".join(location) or "_"] = err["msg"]

        return JSONResponse(
            status_code=422,
            content=_envelope(
                message="Validation failed",
                code="VALIDATION_ERROR",
                request=request,
                details=field_errors,
            ),
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        """A race that beat an application-level check — for example two
        simultaneous registrations of the same email. Reported as a conflict
        rather than a 500, without echoing the database's message back."""
        logger.warning("integrity error on %s: %s", request.url.path, exc.orig)
        return JSONResponse(
            status_code=409,
            content=_envelope(
                message="That operation conflicts with existing data.",
                code="CONFLICT",
                request=request,
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database error on %s", request.url.path)
        return JSONResponse(
            status_code=503,
            content=_envelope(
                message="A database error occurred. Please retry.",
                code="DATABASE_ERROR",
                request=request,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Starlette's own 404/405 etc., rewrapped so even framework-generated
        errors match the project envelope."""
        codes = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                message=str(exc.detail),
                code=codes.get(exc.status_code, "HTTP_ERROR"),
                request=request,
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """The catch-all. A genuine defect: log the full trace server-side,
        return nothing useful to the client.

        In production the message is generic — an exception string can leak
        table names, file paths and query fragments. The request_id is the
        bridge: the user quotes it, you find the trace.
        """
        logger.exception("unhandled exception on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope(
                message=(
                    f"{type(exc).__name__}: {exc}"
                    if settings.DEBUG
                    else "Internal server error."
                ),
                code="INTERNAL_ERROR",
                request=request,
            ),
        )
