"""Scheduled maintenance, triggered by Vercel Cron.

Serverless has no worker process, so anything that must happen without a user
present has to arrive as an HTTP request. Vercel Cron makes that request; this
module is what it calls.

Not mounted under the versioned API prefix: these are operational endpoints for
the platform, not part of the product's contract, and nothing outside this
deployment should be written against them.
"""

from __future__ import annotations

import datetime as dt
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.core.config import settings
from app.core.exceptions import UnauthorizedError
from app.db.session import get_db
from app.lib.logging import get_logger
from app.lib.responses import ApiResponse
from app.models.match_history import InvoiceStatus
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.match_history_repository import MatchHistoryRepository
from app.services.approval_service import nudge_overdue_approval
from app.services.notification_service import NotificationService
from app.services.invoice_service import InvoiceService
from app.services.match_service import run_matching_for_invoice
from app.services.ocr_service import run_ocr_for_invoice

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/cron", tags=["internal"])



def _authorise(authorization: str | None) -> None:
    """Vercel sends `Authorization: Bearer $CRON_SECRET` when the var is set.

    Refuses outright when no secret is configured rather than running open: an
    endpoint that re-queues work is a free way to spend somebody else's Mistral
    budget, and "we forgot to set it" must not be the same as "it is public".
    """
    expected = settings.CRON_SECRET.get_secret_value()
    if not expected:
        raise UnauthorizedError("CRON_SECRET is not configured.")

    supplied = (authorization or "").removeprefix("Bearer ").strip()
    # Constant-time: a plain `!=` leaks the secret's prefix to anything that can
    # measure the response.
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise UnauthorizedError("Invalid cron credentials.")


@router.get(
    "/sweep",
    status_code=status.HTTP_200_OK,
    summary="Re-queue invoices whose processing was cut short",
    include_in_schema=False,
)
async def sweep(
    db: Annotated[AsyncSession, Depends(get_db)],
    background: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> ApiResponse[dict[str, int]]:
    """Find invoices stalled mid-pipeline and start them again.

    This is the "Re-read document" / "Re-run matching" button an admin would
    press, automated — the same entry points, so a swept invoice takes exactly
    the path a manual retry would.

    DELIBERATELY CROSS-COMPANY, and the only endpoint that is. There is no
    caller to scope it to: the request comes from a scheduler holding a shared
    secret, not from a person in a company, and a stalled invoice in one
    company must not stay stalled because nobody in another triggered a sweep.

    That is safe because the sweep never READS anything company-specific. It
    selects rows by status and age, then hands each one's id to the same task a
    manual retry would use — and that task loads the invoice itself and takes
    its company from the row. So every re-queued invoice carries its own
    company with it, one at a time, and no company context is ever shared
    between two of them or inherited from the process.
    """
    _authorise(authorization)

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(
        minutes=settings.STUCK_AFTER_MINUTES
    )
    stuck = await MatchHistoryRepository(db).find_stuck(
        older_than=cutoff,
        # Extraction is normally started by the uploading client, one call per
        # invoice. This is the net under that: an upload whose client went away
        # before it could make the call is picked up here instead of sitting in
        # `uploaded` forever, looking for all the world like a finished one.
        include_unstarted=InvoiceService.extraction_enabled(),
    )

    requeued = 0
    for invoice in stuck:
        # Which half of the pipeline died decides where to resume. An invoice
        # that was matching has already been read, and re-reading it would pay
        # Mistral twice for the same page.
        if invoice.status is InvoiceStatus.MATCHING:
            background.add_task(run_matching_for_invoice, invoice.id)
        else:
            background.add_task(run_ocr_for_invoice, invoice.id)
        requeued += 1
        # The company is logged, not passed: the task re-reads it from the row.
        # It is here so a sweep can be audited per company after the fact.
        logger.warning(
            "Sweeping invoice %s (company %s): stuck in %s since %s",
            invoice.id,
            invoice.company_id,
            invoice.status.value,
            invoice.updated_at,
        )

    if requeued:
        logger.info("Cron sweep re-queued %d invoice(s)", requeued)

    # ---------------------------------------------------- overdue approvals
    #
    # The same dispatcher shape, for the same reason. `find_overdue` selects ids
    # and only ids; each one goes to a task that loads the row, takes the
    # company from it, and notifies inside that company alone. Nothing
    # company-specific is read here, so the argument above still holds.
    #
    # An approval chain's real failure is being forgotten, not being refused: a
    # request sits on somebody who is on leave and the invoice surfaces weeks
    # later when the vendor chases it. This is what makes anybody notice.
    now = dt.datetime.now(dt.UTC)
    overdue = await ApprovalRepository(db).find_overdue(
        waiting_since=now
        - dt.timedelta(hours=settings.APPROVAL_REMIND_AFTER_HOURS),
        nudged_before=now
        - dt.timedelta(hours=settings.APPROVAL_REMIND_AFTER_HOURS),
    )
    for request_id in overdue:
        background.add_task(nudge_overdue_approval, request_id)

    if overdue:
        logger.info("Cron sweep nudged %d overdue approval(s)", len(overdue))

    return ApiResponse.ok(
        {
            "requeued": requeued,
            "examined": len(stuck),
            "approvals_nudged": len(overdue),
        }
    )


@router.get(
    "/cleanup",
    response_model=ApiResponse[dict[str, int]],
    summary="Delete read notifications past their retention window",
    include_in_schema=False,
)
async def cleanup(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> ApiResponse[dict[str, int]]:
    """Housekeeping. Nothing here reads a row — it only removes them by age.

    Its own endpoint rather than another branch of `/sweep`, and on its own
    schedule. The sweep runs every five minutes because a stalled invoice is
    somebody waiting; a DELETE that scans by age has no business running 288
    times a day to find nothing.

    Cross-company, like the sweep, because a scheduler has no company to scope
    to — and safe for a stronger reason than the sweep's. The sweep's argument
    is that it reads nothing company-SPECIFIC; this one reads nothing at all.
    Age and read-state are the entire predicate, and no company's rows can be
    exposed to another by a statement that returns none.

    Notifications are the only thing removed. What actually happened is on the
    invoice, the approval request and its decisions; this table is the nudge
    that pointed at them.
    """
    _authorise(authorization)

    removed = await NotificationService(db).purge_read(
        older_than_days=settings.NOTIFICATION_RETENTION_DAYS
    )
    if removed:
        logger.info(
            "Cron cleanup removed %d read notification(s) older than %d days",
            removed,
            settings.NOTIFICATION_RETENTION_DAYS,
        )
    return ApiResponse.ok(
        {"removed": removed, "retention_days": settings.NOTIFICATION_RETENTION_DAYS}
    )
