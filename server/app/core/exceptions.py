"""Application exception hierarchy.

The Python equivalent of Express's `ApiError`. Every exception carries three
things: the HTTP status, a stable machine-readable `code` the frontend can
branch on, and a human-readable message safe to show a user.

Idiomatic difference from Express worth noting: there is no `asyncHandler`
wrapper here and none is needed. Express requires it because a rejected promise
in an async handler is not routed to the error middleware automatically.
Starlette awaits every endpoint inside its own try/except, so an exception
raised anywhere in a request — including deep inside a repository — propagates
to the handlers registered in `main.py` on its own. Raising is the mechanism.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base for every expected failure.

    Raise these freely from any layer. `main.py` translates them into the
    project's standard error envelope.
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.details = details
        self.headers = headers
        super().__init__(self.message)


# --------------------------------------------------------------------------
# Generic
# --------------------------------------------------------------------------
class BadRequestError(AppError):
    status_code, code, message = 400, "BAD_REQUEST", "Invalid request."


class ValidationError(AppError):
    status_code, code, message = 422, "VALIDATION_ERROR", "Validation failed."


class NotFoundError(AppError):
    status_code, code, message = 404, "NOT_FOUND", "Resource not found."


class ConflictError(AppError):
    status_code, code, message = 409, "CONFLICT", "Resource conflict."


# --------------------------------------------------------------------------
# Authentication (401) — "we do not know who you are"
# --------------------------------------------------------------------------
class UnauthorizedError(AppError):
    status_code, code, message = 401, "UNAUTHORIZED", "Authentication required."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        # RFC 6750: a 401 on a Bearer-protected resource must carry this header.
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class InvalidCredentialsError(UnauthorizedError):
    code = "INVALID_CREDENTIALS"
    message = "Invalid email or password."


class InvalidTokenError(UnauthorizedError):
    code = "INVALID_TOKEN"
    message = "Invalid or malformed token."


class TokenExpiredError(UnauthorizedError):
    code = "TOKEN_EXPIRED"
    message = "Token has expired."


class InvalidRefreshTokenError(UnauthorizedError):
    code = "INVALID_REFRESH_TOKEN"
    message = "Refresh token is missing, invalid, or expired."


class RefreshTokenReusedError(UnauthorizedError):
    """A refresh token was presented that had already been rotated away.

    Treated as theft: the legitimate client would be holding the newest token,
    so seeing an old one means someone captured it. The service revokes the
    whole session family in response.
    """

    code = "REFRESH_TOKEN_REUSED"
    message = "Refresh token reuse detected. All sessions have been revoked."


class InactiveUserError(UnauthorizedError):
    code = "INACTIVE_USER"
    message = "This account is disabled."


# --------------------------------------------------------------------------
# Authorization (403) — "we know who you are, and you may not"
# --------------------------------------------------------------------------
class ForbiddenError(AppError):
    status_code, code, message = 403, "FORBIDDEN", "You do not have permission."


class InsufficientRoleError(ForbiddenError):
    code = "INSUFFICIENT_ROLE"
    message = "Your role does not permit this action."


class InsufficientPermissionError(ForbiddenError):
    code = "INSUFFICIENT_PERMISSION"
    message = "You lack the required permission."


# --------------------------------------------------------------------------
# Domain-specific
# --------------------------------------------------------------------------
class EmailAlreadyRegisteredError(ConflictError):
    code = "EMAIL_ALREADY_REGISTERED"
    message = "An account with this email already exists."


class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"
    message = "User not found."


# --------------------------------------------------------------------------
# File upload / object storage
# --------------------------------------------------------------------------
class EmptyFileError(BadRequestError):
    code = "EMPTY_FILE"
    message = "The uploaded file is empty."


class FileTooLargeError(AppError):
    """413. Raised while streaming, before the whole body is buffered."""

    status_code, code = 413, "FILE_TOO_LARGE"
    message = "The uploaded file exceeds the maximum allowed size."


class UnsupportedFileTypeError(AppError):
    """415. Decided by content sniffing, not by the client's Content-Type."""

    status_code, code = 415, "UNSUPPORTED_FILE_TYPE"
    message = "Unsupported file type. Allowed: PDF, PNG, JPEG, TIFF."


class StorageError(AppError):
    """502. Object storage is a separate upstream service; when it fails the
    fault is ours-to-them, not the client's, so this must never surface as a
    400. Carries no provider detail — bucket names and keys are not the
    client's business."""

    status_code, code = 502, "STORAGE_ERROR"
    message = "File storage is temporarily unavailable. Please try again."


class StorageNotConfiguredError(AppError):
    """503. The R2 credentials are absent. Deliberately distinct from
    StorageError: this is a deployment mistake, not a transient outage, and
    retrying will never fix it."""

    status_code, code = 503, "STORAGE_NOT_CONFIGURED"
    message = "File storage is not configured on this server."


# --------------------------------------------------------------------------
# OCR / AI extraction
# --------------------------------------------------------------------------
class OcrNotConfiguredError(AppError):
    """503. No Mistral API key. A deployment mistake, not an outage."""

    status_code, code = 503, "OCR_NOT_CONFIGURED"
    message = "Document extraction is not configured on this server."


class OcrError(AppError):
    """502. Mistral failed or returned something unusable.

    Never carries the provider's raw message: it can contain the signed URL of
    the document, which is a credential for the duration of its TTL.
    """

    status_code, code = 502, "OCR_ERROR"
    message = "Document extraction failed. Please try again."


class ExtractionInvalidError(AppError):
    """422. The model returned JSON that does not satisfy the schema.

    Distinct from OcrError on purpose: the call succeeded and was billed, the
    output is simply unusable. Retrying may well produce a valid result, but
    the failure is about content rather than transport.
    """

    status_code, code = 422, "EXTRACTION_INVALID"
    message = "The document could not be read into the expected format."


# --------------------------------------------------------------------------
# Odoo
# --------------------------------------------------------------------------
class OdooNotConfiguredError(AppError):
    status_code, code = 503, "ODOO_NOT_CONFIGURED"
    message = "Odoo is not configured on this server."


class OdooAuthError(AppError):
    """502, not 401. The caller's credentials are fine — OURS are wrong.

    Returning 401 here would tell the user to sign in again for a problem no
    action of theirs can fix.
    """

    status_code, code = 502, "ODOO_AUTH_ERROR"
    message = "Could not authenticate with Odoo. Check the server credentials."


class OdooError(AppError):
    """502. Odoo is an upstream service; its faults are ours-to-them.

    Carries no Odoo detail — a fault message can include the database name and
    internal model paths, which are not the client's business.
    """

    status_code, code = 502, "ODOO_ERROR"
    message = "Odoo is temporarily unavailable. Please try again."


class OdooRefusedError(AppError):
    """409. Odoo understood the request and said no.

    A different thing entirely from `OdooError`, and the distinction is not
    cosmetic. Odoo answers a `UserError` with XML-RPC fault code 2 and a bare
    message — no traceback — because that message was written to be shown to a
    person: "complete the quality inspection first", "the period is locked",
    "this order is already fully invoiced".

    Reporting those as 502 "temporarily unavailable, please try again" is worse
    than unhelpful. It is false in both halves: Odoo is up, and retrying will
    never work, because nothing about waiting changes an unfinished quality
    check. So this is a 409 and it carries Odoo's own wording.

    Echoing that wording is safe precisely because fault code 2 is the case
    where Odoo has already decided the text is fit for a user. Fault code 1 —
    an actual traceback, with the database name and internal paths in it —
    stays behind `OdooError` and is only ever logged.
    """

    status_code, code = 409, "ODOO_REFUSED"
    message = "Odoo refused this operation."


# --------------------------------------------------------------------------
# Billing
#
# All 409s, and all raised BEFORE the one irreversible call in the billing
# flow (`stock.picking.button_validate`). That ordering is the point: a refusal
# after the goods are marked received leaves a warehouse claim this system
# cannot retract, so every one of these has to be reachable while nothing has
# been written yet.
# --------------------------------------------------------------------------
class OverBilledError(AppError):
    """409. The invoice asks for more than the order has left to bill.

    `details` carries the offending lines with their remaining quantities:
    "over-billed" without saying by how much on which line leaves the reviewer
    to work it out against Odoo, which is what this product exists to avoid.
    """

    status_code, code = 409, "PO_LINE_OVER_BILLED"
    message = "This invoice bills more than the purchase order has left."


class ReceiptNotPossibleError(AppError):
    """409. The receipt cannot be recorded automatically — and nothing was.

    Raised for a lot/serial-tracked product, an ambiguous set of open
    receipts, or quantities Odoo did not accept as written. In every case the
    guarantee in the message is literal: no stock has moved.
    """

    status_code, code = 409, "RECEIPT_NOT_POSSIBLE"
    message = "The goods receipt could not be recorded automatically."


class NothingToBillError(AppError):
    """409. Odoo has nothing left to invoice on this order.

    `action_create_invoice` skips an order whose `invoice_status` is not
    "to invoice" and answers with a window-close action — no fault, no bill.
    Without this the caller would see a successful call that created nothing.
    """

    status_code, code = 409, "NOTHING_TO_BILL"
    message = "Odoo has nothing left to bill on this purchase order."


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------
class InvoiceNotReadyError(AppError):
    """409. The requested step needs an earlier one to have finished.

    Matching before extraction has nothing to match on, so this is a conflict
    with the row's current state rather than a bad request.
    """

    status_code, code = 409, "INVOICE_NOT_READY"
    message = "This invoice is not ready for that step yet."
