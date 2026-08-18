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
from app.repositories.match_history_repository import MatchHistoryRepository
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
        logger.warning(
            "Sweeping invoice %s: stuck in %s since %s",
            invoice.id,
            invoice.status.value,
            invoice.updated_at,
        )

    if requeued:
        logger.info("Cron sweep re-queued %d invoice(s)", requeued)
    return ApiResponse.ok({"requeued": requeued, "examined": len(stuck)})
