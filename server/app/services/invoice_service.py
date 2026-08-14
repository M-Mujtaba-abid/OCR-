"""Invoice intake business rules.

The upload path touches two systems that cannot participate in one transaction:
object storage and Postgres. Everything careful in this module is about that.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import BackgroundTasks, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.config import settings
from app.core.exceptions import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.lib.logging import get_logger
from app.models.match_history import (
    OPEN_STATUSES,
    WITHDRAWABLE_STATUSES,
    InvoiceStatus,
    MatchHistory,
)
from app.models.notification import NotificationType
from app.models.user import User, UserRole
from app.repositories.match_history_repository import MatchHistoryRepository
from app.schemas.invoice import InvoiceStats, UploadRejection
from app.services.notification_service import NotificationService
from app.services.ocr_service import run_ocr_for_invoice

logger = get_logger(__name__)

#: Where invoices live inside the bucket. One top-level folder so a lifecycle
#: rule or an export can target the whole class of object by prefix.
INVOICE_FOLDER = "invoices"

#: How long a download link stays valid. Long enough to open a PDF viewer,
#: short enough that a link pasted into a chat is dead by the time it is read.
DOWNLOAD_URL_TTL = 300


class InvoiceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.invoices = MatchHistoryRepository(db)
        self.notifications = NotificationService(db)

    # ------------------------------------------------------------------ upload
    async def upload_invoices(
        self,
        *,
        user: User,
        files: list[UploadFile],
        member_ref_no: str | None = None,
        member_notes: str | None = None,
        tenant_id: str = "default",
    ) -> tuple[list[MatchHistory], list[UploadRejection]]:
        """Store 1..N invoices and queue them for an admin.

        **Partial success is a first-class outcome.** Nine good PDFs and one
        Word document must not fail the whole request — the member would have
        to work out which file was the problem and re-upload everything. Each
        file is validated and stored independently; failures come back as a
        structured rejection list.

        **Ordering: storage first, database second.** The reverse would leave a
        row pointing at an object that was never written, which reads as
        corruption. This way the worst case is an orphaned object, which is
        invisible to users and cleanable by a lifecycle rule.

        **The commit is the commit point.** If it fails, every object written
        during this request is deleted before the error propagates — otherwise
        a retry would double the storage for every attempt.
        """
        if not files:
            raise BadRequestError(
                "Attach at least one file.", code="NO_FILES"
            )

        if len(files) > settings.MAX_FILES_PER_UPLOAD:
            raise BadRequestError(
                f"Upload at most {settings.MAX_FILES_PER_UPLOAD} files at a time. "
                f"You sent {len(files)}.",
                code="TOO_MANY_FILES",
            )

        stored_keys: list[str] = []
        created: list[MatchHistory] = []
        rejected: list[UploadRejection] = []

        try:
            for upload in files:
                display_name = upload.filename or "unnamed"
                try:
                    result = await storage.upload_file(
                        upload, INVOICE_FOLDER, tenant_id=tenant_id
                    )
                except AppError as exc:
                    # Only a CLIENT fault is a per-file rejection: a corrupt
                    # PDF says nothing about the next file, so the other nine
                    # should still land.
                    #
                    # A 5xx is the opposite. Storage being down or
                    # misconfigured will fail every file identically, and
                    # reporting that as ten individual rejections would tell
                    # the member their files were bad when the fault is ours.
                    # Let it propagate so the caller gets one honest 502/503.
                    if exc.status_code >= 500:
                        raise

                    logger.info(
                        "Rejected %s for %s: %s", display_name, user.id, exc.code
                    )
                    rejected.append(
                        UploadRejection(
                            file_name=display_name,
                            reason=exc.message,
                            code=exc.code,
                        )
                    )
                    continue

                stored_keys.append(result.key)
                created.append(
                    await self.invoices.create(
                        tenant_id=tenant_id,
                        uploaded_by=user.id,
                        member_ref_no=member_ref_no,
                        member_notes=member_notes,
                        file_name=result.original_name,
                        file_key=result.key,
                        file_url=result.url,
                        file_size_bytes=result.size_bytes,
                        mime_type=result.mime_type,
                        status=InvoiceStatus.UPLOADED,
                    )
                )

            if not created:
                # Every file failed. Nothing to commit, and the caller needs a
                # 4xx rather than an empty 201.
                raise BadRequestError(
                    "None of the files could be accepted.",
                    code="NO_VALID_FILES",
                    details=[r.model_dump() for r in rejected],
                )

            who = (user.full_name or "").strip() or user.email
            await self.notifications.notify_admins(
                type=NotificationType.INVOICE_UPLOADED,
                title=(
                    f"{len(created)} new invoice{'s' if len(created) > 1 else ''} "
                    f"from {who}"
                ),
                message=(
                    f"{who} uploaded {len(created)} file"
                    f"{'s' if len(created) > 1 else ''}"
                    + (f" (ref {member_ref_no})" if member_ref_no else "")
                ),
                # Only meaningful for a single upload; for a batch the admin
                # opens the queue rather than one specific row.
                match_history_id=created[0].id if len(created) == 1 else None,
                tenant_id=tenant_id,
            )

            await self.db.commit()

        except Exception:
            await self.db.rollback()
            # Best effort, and deliberately not allowed to mask the real error:
            # a failed cleanup leaves an orphan, which is a storage-cost
            # problem, not a correctness one.
            for key in stored_keys:
                await storage.delete_file(key)
            raise

        for invoice in created:
            await self.db.refresh(invoice, attribute_names=["uploader"])

        logger.info(
            "User %s uploaded %d invoice(s), %d rejected",
            user.id,
            len(created),
            len(rejected),
        )
        return created, rejected

    @staticmethod
    def schedule_extraction(
        background: BackgroundTasks, invoices: Sequence[MatchHistory]
    ) -> None:
        """Queue OCR for freshly uploaded invoices.

        Called by the controller **after** the response body has been built, so
        the member is not made to wait 5–20 seconds per file for Mistral.

        Two things this deliberately does not do:

          * It does not pass the ORM objects along. The task receives an id and
            loads the row itself, because this request's session — and every
            object attached to it — is closed the moment the response is sent.
          * It does not run before the commit. A task that started first could
            read a row that a rollback then removed.
        """
        if not settings.OCR_AUTO_ON_UPLOAD:
            # The kill switch. Uploads still land; extraction waits for an
            # admin to trigger it, at no Mistral cost.
            logger.info("OCR_AUTO_ON_UPLOAD is off — %d invoice(s) left queued", len(invoices))
            return

        if not settings.is_ocr_configured:
            logger.warning("No Mistral key — %d invoice(s) left unextracted", len(invoices))
            return

        for invoice in invoices:
            background.add_task(run_ocr_for_invoice, invoice.id)

    async def mark_status(
        self, invoice: MatchHistory, status: InvoiceStatus
    ) -> MatchHistory:
        """Move a row to a status and commit immediately.

        Used to claim an invoice for a background job while still inside the
        request. Committing here rather than in the task is what makes the 202
        honest: by the time the client receives it and polls, the row already
        reads as in-flight.
        """
        await self.invoices.update(invoice, status=status)
        await self.db.commit()
        return invoice

    # ------------------------------------------------------------------- reads
    async def list_own(
        self,
        *,
        user: User,
        page: int = 1,
        page_size: int = 20,
        status: InvoiceStatus | None = None,
    ) -> tuple[list[MatchHistory], int]:
        items = await self.invoices.list_for_user(
            user.id,
            limit=page_size,
            offset=(page - 1) * page_size,
            status=status,
        )
        total = await self.invoices.count(user_id=user.id, status=status)
        return items, total

    async def list_all(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: InvoiceStatus | None = None,
        open_only: bool = False,
        uploaded_by: uuid.UUID | None = None,
        tenant_id: str = "default",
    ) -> tuple[list[MatchHistory], int]:
        items = await self.invoices.list_all(
            tenant_id=tenant_id,
            limit=page_size,
            offset=(page - 1) * page_size,
            status=status,
            open_only=open_only,
            uploaded_by=uploaded_by,
        )
        total = await self.invoices.count(
            tenant_id=tenant_id, status=status, open_only=open_only
        )
        return items, total

    async def get_for_user(
        self,
        *,
        invoice_id: uuid.UUID,
        user: User,
        can_read_all: bool,
        with_lines: bool = False,
    ) -> MatchHistory:
        """Fetch one invoice, enforcing ownership.

        A member may read only their own. The check is here rather than in the
        route because the same rule has to hold for the file-link endpoint, and
        a rule written twice is a rule that will eventually differ.

        NotFoundError, not ForbiddenError, when a member asks for someone
        else's: a 403 would confirm the id exists.
        """
        invoice = await self.invoices.find_by_id(invoice_id, with_lines=with_lines)
        if invoice is None:
            raise NotFoundError("Invoice not found.", code="INVOICE_NOT_FOUND")

        if not can_read_all and invoice.uploaded_by != user.id:
            raise NotFoundError("Invoice not found.", code="INVOICE_NOT_FOUND")

        return invoice

    async def get_download_url(
        self, *, invoice_id: uuid.UUID, user: User, can_read_all: bool
    ) -> tuple[MatchHistory, str, int]:
        """A short-lived signed URL for the stored PDF.

        The bucket is private, so this is the only way to read an object. The
        URL is minted per request and never stored — a signed URL in a database
        column outlives its own signature.
        """
        invoice = await self.get_for_user(
            invoice_id=invoice_id, user=user, can_read_all=can_read_all
        )
        url = await storage.generate_presigned_url(invoice.file_key, DOWNLOAD_URL_TTL)
        return invoice, url, DOWNLOAD_URL_TTL

    async def get_stats(
        self, *, user: User | None = None, tenant_id: str = "default"
    ) -> InvoiceStats:
        """Counts per status. Scoped to one user when `user` is given."""
        by_status = await self.invoices.count_by_status(
            tenant_id=tenant_id, user_id=user.id if user else None
        )
        return InvoiceStats(
            total=sum(by_status.values()),
            # Zero-filled so the dashboard renders a stable set of cards
            # instead of ones that appear and disappear as data changes.
            by_status={status: by_status.get(status, 0) for status in InvoiceStatus},
            open_count=sum(by_status.get(status, 0) for status in OPEN_STATUSES),
        )

    # ------------------------------------------------------------------ delete
    async def delete(self, *, invoice_id: uuid.UUID, actor: User) -> None:
        """Remove an invoice and its stored file.

        Members may withdraw their own upload only while it is still
        `uploaded` — once processing starts it is part of an audit trail and
        deleting it would leave a batch referencing nothing.
        """
        invoice = await self.invoices.find_by_id(invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found.", code="INVOICE_NOT_FOUND")

        is_admin = actor.role is UserRole.ADMIN
        if not is_admin:
            if invoice.uploaded_by != actor.id:
                raise NotFoundError("Invoice not found.", code="INVOICE_NOT_FOUND")
            if invoice.status not in WITHDRAWABLE_STATUSES:
                raise ForbiddenError(
                    "This invoice is already under review and can no longer be "
                    "withdrawn.",
                    code="INVOICE_LOCKED",
                )

        key = invoice.file_key
        await self.db.delete(invoice)
        await self.db.commit()

        # After the commit, not before: if the delete were first and the commit
        # then failed, the row would survive pointing at a file that is gone.
        await storage.delete_file(key)
        logger.info("Invoice %s deleted by %s", invoice_id, actor.id)
