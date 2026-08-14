"""Match an extracted invoice to an Odoo purchase order.

Two stages, and the split is the whole design:

  1. **Narrow, in code.** `matching_engine` scores every open order and returns
     the plausible handful. Deterministic, free, and it scales — 5 000 open
     orders cost the same prompt as 15.
  2. **Decide, with the model.** The LLM sees only that shortlist and picks,
     with a stated reason. Judgement is what it is good at; search is not.

Skipping stage 1 and handing the model every order would be simpler code and a
worse system: the prompt grows without bound, the cost with it, and a model
asked to find one row among hundreds reliably overlooks it.

The result is always a suggestion. `pending_review`, never `confirmed` — a
machine does not post a vendor bill on its own.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import mistral
from app.core.config import settings
from app.core.exceptions import AppError, InvoiceNotReadyError
from app.db.session import SessionFactory
from app.lib.logging import get_logger
from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.notification import NotificationType
from app.repositories.match_history_repository import MatchHistoryRepository
from app.schemas.extraction import InvoiceExtraction
from app.schemas.matching import MatchVerdict
from app.schemas.odoo import OdooPurchaseOrder
from app.services import matching_engine
from app.services.notification_service import NotificationService
from app.services.odoo_service import odoo_service

logger = get_logger(__name__)

RERANK_PROMPT = """\
You are an accounts-payable clerk deciding which purchase order a vendor
invoice belongs to.

You are given one invoice and a shortlist of candidate purchase orders that
were pre-filtered by a scoring pass. Each candidate carries that pass's score
and its breakdown, which you may use as a prior but must not treat as the
answer — the scores are a heuristic and you can see things they cannot.

Weigh the evidence in roughly this order:

1. An explicit reference match. A vendor quoting the PO number is stating the
   answer, unless the amounts flatly contradict it.
2. Vendor identity. Legal-form differences ("Ltd" vs "Limited") and word order
   do not make two vendors different; a genuinely different company does.
3. Amounts. Compare untaxed totals — tax treatment routinely differs between a
   purchase order and the invoice for it, while the goods do not.
4. Line items. Descriptions are reworded constantly between a catalogue and a
   vendor's invoice; judge whether they describe the same goods.
5. Dates. An invoice normally follows its order by days or weeks.

Rules:
- `matched_po_id` MUST be one of the po_id values in the candidate list, copied
  exactly. Never invent an id and never return one that is not listed.
- If no candidate is genuinely the right order, return null. A wrong match
  costs an accountant far more time than no match does.
- A partial delivery — the invoice covering some of an order's lines — is still
  a match to that order. Say so in the reasoning.
- Be conservative with confidence. Reserve above 90 for a reference match or an
  otherwise unambiguous case.
"""


async def run_matching_for_invoice(invoice_id: uuid.UUID) -> None:
    """Match one invoice. Never raises — every outcome is a status.

    Runs as a background task and opens its own session: the request-scoped one
    is closed by the time this executes.
    """
    async with SessionFactory() as db:
        repo = MatchHistoryRepository(db)
        invoice = await repo.find_by_id(invoice_id)

        if invoice is None:
            logger.info("Matching skipped: invoice %s no longer exists", invoice_id)
            return
        if not invoice.extracted_json:
            await _fail(db, repo, invoice, "The invoice has not been read yet.")
            return

        # The caller normally claims the row before scheduling this, so that the
        # 202 it returns is already true. Set it here too for the paths that do
        # not — a retry, or a future scheduler — so the state machine holds
        # regardless of who started the job.
        if invoice.status is not InvoiceStatus.MATCHING:
            await repo.update(invoice, status=InvoiceStatus.MATCHING)
            await db.commit()

        try:
            await _match(db, repo, invoice)
        except AppError as exc:
            await _fail(db, repo, invoice, exc.message)
        except Exception:
            logger.exception("Matching crashed for invoice %s", invoice_id)
            await _fail(db, repo, invoice, "An unexpected error occurred while matching.")


async def _match(
    db: AsyncSession, repo: MatchHistoryRepository, invoice: MatchHistory
) -> None:
    extraction = InvoiceExtraction.model_validate(invoice.extracted_json)

    orders = await odoo_service.fetch_open_purchase_orders()
    candidates = matching_engine.rank(
        extraction,
        orders,
        limit=settings.MATCH_CANDIDATE_LIMIT,
        floor=settings.MATCH_SCORE_FLOOR,
    )

    if not candidates:
        # The model is never asked to choose from an empty list. Given nothing
        # plausible it will invent something plausible-looking, and that costs
        # money to produce a wrong answer.
        await _record_no_match(
            db,
            repo,
            invoice,
            candidates=[],
            reasoning=(
                f"None of the {len(orders)} open purchase orders scored above "
                f"the {settings.MATCH_SCORE_FLOOR:.0f} threshold."
            ),
            strategy="no_candidates",
        )
        return

    verdict = await _rerank(extraction, candidates)

    # The guard that makes the LLM's answer safe to store. A model will
    # occasionally return an id that looks right and was never in the prompt;
    # accepting it would attach an invoice to an unrelated purchase order.
    candidate_ids = {c.order.id for c in candidates}
    chosen: OdooPurchaseOrder | None = None

    if verdict.matched_po_id is not None:
        if verdict.matched_po_id in candidate_ids:
            chosen = next(
                c.order for c in candidates if c.order.id == verdict.matched_po_id
            )
        else:
            logger.warning(
                "Invoice %s: model returned po_id=%s which was not among the %d "
                "candidates — treating as no match",
                invoice.id,
                verdict.matched_po_id,
                len(candidates),
            )

    if chosen is None or verdict.confidence < settings.MATCH_MIN_CONFIDENCE:
        reason = verdict.reasoning
        if chosen is not None:
            reason = (
                f"{verdict.reasoning}\n\n(Confidence {verdict.confidence:.0f} is "
                f"below the {settings.MATCH_MIN_CONFIDENCE:.0f} threshold, so this "
                f"is recorded as unmatched rather than as a weak suggestion.)"
            )
        await _record_no_match(
            db, repo, invoice, candidates=candidates, reasoning=reason,
            strategy="llm_rerank", confidence=verdict.confidence,
        )
        return

    await repo.update(
        invoice,
        status=InvoiceStatus.PENDING_REVIEW,
        matched_po_id=chosen.id,
        matched_po_name=chosen.name,
        confidence_score=verdict.confidence,
        match_strategy="llm_rerank",
        match_reasoning=verdict.reasoning,
        candidates=_candidates_payload(candidates, verdict),
    )
    await NotificationService(db).notify_admins(
        type=NotificationType.MATCH_FOUND,
        title=f"Match found for {invoice.file_name}",
        message=(
            f"{chosen.name} — {chosen.partner_name} "
            f"({verdict.confidence:.0f}% confidence)"
        ),
        match_history_id=invoice.id,
        tenant_id=invoice.tenant_id,
    )
    await db.commit()

    logger.info(
        "Invoice %s matched to %s (%s) at %.0f%% confidence",
        invoice.id,
        chosen.name,
        chosen.id,
        verdict.confidence,
    )


async def _rerank(
    extraction: InvoiceExtraction,
    candidates: list[matching_engine.ScoredCandidate],
) -> MatchVerdict:
    """Ask the model to choose. Returns a validated verdict."""
    payload = {
        "invoice": {
            "vendor_name": extraction.vendor_name,
            "vendor_email": extraction.vendor_email,
            "reference": extraction.po_number,
            "order_date": extraction.order_date,
            "currency": extraction.currency,
            "untaxed_amount": round(extraction.untaxed_amount, 2),
            "tax_amount": round(extraction.tax_amount, 2),
            "total_amount": round(extraction.total_amount, 2),
            "items": [
                {
                    "name": item.name,
                    "quantity": round(item.quantity, 3),
                    "unit_price": round(item.unit_price, 2),
                    "subtotal": round(item.subtotal, 2),
                }
                for item in extraction.items[:25]
            ],
        },
        "candidates": [
            {
                **c.order.for_prompt(),
                "prefilter_score": round(c.score, 1),
                "prefilter_breakdown": {k: round(v) for k, v in c.breakdown.items()},
            }
            for c in candidates
        ],
    }

    raw = await mistral.complete_json(
        system_prompt=RERANK_PROMPT,
        user_content=json.dumps(payload, ensure_ascii=False),
        schema_model=MatchVerdict,
    )
    return mistral.validate_extraction(raw, MatchVerdict)


def _candidates_payload(
    candidates: list[matching_engine.ScoredCandidate],
    verdict: MatchVerdict | None,
) -> dict[str, object]:
    """The audit blob written to `match_history.candidates`.

    Everything the decision was made from, losers included. Without it a wrong
    match is unarguable — nobody can tell whether the right order was even on
    the shortlist.
    """
    rejected = {alt.po_id: alt.why_not for alt in (verdict.alternatives if verdict else [])}
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "strategy": "prefilter+llm_rerank",
        "weights": matching_engine.WEIGHTS,
        "chosen_po_id": verdict.matched_po_id if verdict else None,
        "confidence": round(verdict.confidence, 1) if verdict else None,
        "reasoning": verdict.reasoning if verdict else None,
        "items": [
            {**c.to_json(), "rejected_because": rejected.get(c.order.id)}
            for c in candidates
        ],
    }


async def _record_no_match(
    db: AsyncSession,
    repo: MatchHistoryRepository,
    invoice: MatchHistory,
    *,
    candidates: list[matching_engine.ScoredCandidate],
    reasoning: str,
    strategy: str,
    confidence: float | None = None,
) -> None:
    """No confident match. Still a completed run, not a failure.

    `no_match` is a legitimate outcome an admin acts on by assigning the order
    manually — distinct from `match_failed`, which means the run itself broke.
    """
    await repo.update(
        invoice,
        status=InvoiceStatus.NO_MATCH,
        matched_po_id=None,
        matched_po_name=None,
        confidence_score=confidence,
        match_strategy=strategy,
        match_reasoning=reasoning,
        candidates=_candidates_payload(candidates, None) if candidates else None,
    )
    await NotificationService(db).notify_admins(
        type=NotificationType.NO_MATCH_FOUND,
        title=f"No match for {invoice.file_name}",
        message=reasoning[:500],
        match_history_id=invoice.id,
        tenant_id=invoice.tenant_id,
    )
    await db.commit()
    logger.info("Invoice %s: no match (%s)", invoice.id, strategy)


async def _fail(
    db: AsyncSession, repo: MatchHistoryRepository, invoice: MatchHistory, reason: str
) -> None:
    try:
        await repo.update(
            invoice, status=InvoiceStatus.MATCH_FAILED, match_reasoning=reason[:2000]
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Could not record match failure for invoice %s", invoice.id)
    logger.warning("Matching failed for invoice %s: %s", invoice.id, reason)


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------
async def confirm_match(
    db: AsyncSession,
    *,
    invoice: MatchHistory,
    po_id: int,
    reviewer_id: uuid.UUID,
) -> MatchHistory:
    """Accept a match, or override it with a different purchase order.

    `was_corrected` distinguishes the two, and that flag is the product's most
    valuable signal: it is the record of where the matcher was wrong, and it is
    what any future tuning would be measured against.
    """
    if not invoice.extracted_json:
        raise InvoiceNotReadyError("This invoice has not been read yet.")

    order = await odoo_service.fetch_purchase_order(po_id)
    if order is None:
        raise InvoiceNotReadyError(
            f"Purchase order {po_id} was not found in Odoo.", code="PO_NOT_FOUND"
        )

    corrected = invoice.matched_po_id is not None and invoice.matched_po_id != po_id

    repo = MatchHistoryRepository(db)
    await repo.update(
        invoice,
        status=InvoiceStatus.CORRECTED if corrected else InvoiceStatus.CONFIRMED,
        final_po_id=order.id,
        matched_po_id=order.id,
        matched_po_name=order.name,
        was_corrected=corrected,
        reviewed_by=reviewer_id,
        reviewed_at=dt.datetime.now(dt.UTC),
    )

    if invoice.uploaded_by:
        await NotificationService(db).notify_user(
            user_id=invoice.uploaded_by,
            type=(
                NotificationType.INVOICE_CORRECTED
                if corrected
                else NotificationType.INVOICE_CONFIRMED
            ),
            title=f"{invoice.file_name} was matched",
            message=f"Matched to {order.name} — {order.partner_name}",
            match_history_id=invoice.id,
            tenant_id=invoice.tenant_id,
        )

    await db.commit()
    logger.info(
        "Invoice %s %s to %s by %s",
        invoice.id,
        "corrected" if corrected else "confirmed",
        order.name,
        reviewer_id,
    )
    return invoice


async def reject_invoice(
    db: AsyncSession,
    *,
    invoice: MatchHistory,
    reason: str,
    reviewer_id: uuid.UUID,
) -> MatchHistory:
    """Discard an invoice, with a reason the uploader can see."""
    repo = MatchHistoryRepository(db)
    await repo.update(
        invoice,
        status=InvoiceStatus.REJECTED,
        rejection_reason=reason[:2000],
        reviewed_by=reviewer_id,
        reviewed_at=dt.datetime.now(dt.UTC),
    )

    if invoice.uploaded_by:
        await NotificationService(db).notify_user(
            user_id=invoice.uploaded_by,
            type=NotificationType.INVOICE_REJECTED,
            title=f"{invoice.file_name} was rejected",
            message=reason[:500],
            match_history_id=invoice.id,
            tenant_id=invoice.tenant_id,
        )

    await db.commit()
    return invoice
