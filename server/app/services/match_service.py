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
from app.services.odoo_service import OdooService, odoo_for_invoice

logger = get_logger(__name__)

RERANK_PROMPT = """\
You are an accounts-payable clerk deciding which purchase order a vendor
invoice belongs to.

You are given one invoice and a shortlist of candidate purchase orders that
were pre-filtered by a scoring pass. Each candidate carries that pass's score
and its breakdown, which you may use as a prior but must not treat as the
answer — the scores are a heuristic and you can see things they cannot.

`prefilter` is that pass's score breakdown, written compactly: `v` vendor,
`a` amount, `r` reference, `d` date, `l` line items, each 0-100. A component
that could not be evaluated is absent rather than zero.

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
- `invoice_status` tells you whether Odoo still expects a bill for the order.
  "invoiced" means one already exists. That does NOT disqualify the order — a
  vendor may be billing late, re-sending, or double-billing — so judge it on
  the evidence like any other candidate, and when you choose one, state
  plainly in the reasoning that a bill already exists and this may be a
  duplicate.
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


async def _orders_to_consider(
    odoo: OdooService,
    extraction: InvoiceExtraction,
) -> list[OdooPurchaseOrder]:
    """The orders this invoice is scored against.

    The billable ones, plus — within a window around the invoice's own date —
    the ones Odoo has already invoiced.

    That second group is not optional. Odoo flips `invoice_status` to
    "invoiced" the moment a bill exists, so an invoice arriving after that
    scored against the billable set alone finds nothing, and the review screen
    reports "no purchase order scored highly enough" while the right order sits
    in Odoo scoring in the nineties. Filtering before scoring turns "already
    billed" into "does not exist", and those need very different answers from a
    reviewer — the second is a missing order, the first is a possible duplicate
    bill.

    They are scored, never preferred: `invoice_status` travels with each
    candidate so the model and the screen can both say what they are looking at.
    """
    orders = await odoo.fetch_open_purchase_orders()

    lookback = settings.MATCH_CLOSED_LOOKBACK_DAYS
    if lookback <= 0:
        return orders

    # Anchored to the invoice's date when it has one — an invoice dated four
    # months ago should look four months back, not ninety days from today.
    anchor = extraction.order_date_value or dt.date.today()
    billed = await odoo.fetch_recently_billed_orders(
        since=anchor - dt.timedelta(days=lookback)
    )

    seen = {order.id for order in orders}
    orders.extend(order for order in billed if order.id not in seen)
    return orders


def _shortlist_for_prompt(
    candidates: list[matching_engine.ScoredCandidate],
) -> list[matching_engine.ScoredCandidate]:
    """The candidates worth paying to describe to the model.

    Distinct from the shortlist itself, which the review screen keeps in full —
    "was the right order even considered?" is the question the stored losers
    exist to answer, and answering it costs nothing. The prompt is a different
    question: a candidate 25 points behind the leader is not what the model
    picks, it is only what the model is billed for.

    Self-adjusting, which is the point. Where the top of the list is tied
    nothing is trimmed and the full spend happens on the decision that needs
    it; where one candidate is far ahead the prompt collapses to the floor.
    """
    leader = candidates[0].score
    kept = [c for c in candidates if c.score >= leader - settings.MATCH_PROMPT_MARGIN]
    if len(kept) >= settings.MATCH_PROMPT_MIN:
        return kept
    # A shortlist of one is a decision already taken — the model cannot
    # disagree with a choice it was never offered.
    return candidates[: settings.MATCH_PROMPT_MIN]


def _beyond_argument(
    candidates: list[matching_engine.ScoredCandidate],
) -> matching_engine.ScoredCandidate | None:
    """The candidate whose case the model could only agree with, if there is one.

    Deliberately narrow. The gate is an EXACT reference match — the vendor
    quoting the order's own number, which the prompt itself calls stating the
    answer — and not the 85-point containment hit, which is a resemblance.
    On top of that, a high score and clear daylight to the runner-up.

    An already-invoiced order never qualifies however well it scores: that is
    the possible-duplicate case, and it is exactly the one worth a second
    opinion before anybody is asked to confirm it.
    """
    leader = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0

    if (
        leader.breakdown.get("reference") == 100.0
        and leader.score >= settings.MATCH_AUTO_ACCEPT_SCORE
        and leader.score - runner_up >= settings.MATCH_AUTO_ACCEPT_MARGIN
        and leader.order.invoice_status != "invoiced"
    ):
        return leader
    return None


async def _match(
    db: AsyncSession, repo: MatchHistoryRepository, invoice: MatchHistory
) -> None:
    extraction = InvoiceExtraction.model_validate(invoice.extracted_json)

    # The invoice decides which Odoo is asked. Every order below therefore
    # comes from the company that owns this invoice, and cannot come from
    # another one — see `odoo_for_invoice`.
    odoo = await odoo_for_invoice(db, invoice)
    orders = await _orders_to_consider(odoo, extraction)
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
                f"None of the {len(orders)} purchase orders considered — open "
                f"and already billed — scored above the "
                f"{settings.MATCH_SCORE_FLOOR:.0f} threshold."
            ),
            strategy="no_candidates",
        )
        return

    settled = _beyond_argument(candidates)
    if settled is not None:
        # No request is made. Everything after this point runs exactly as it
        # does for a model verdict — the same id guard, the same persistence,
        # the same review screen — so the only difference is the bill.
        verdict = MatchVerdict(
            matched_po_id=settled.order.id,
            confidence=round(settled.score, 1),
            reasoning=(
                "Matched without asking the model: the invoice quotes this "
                "order's reference exactly, and no other candidate comes "
                "close.\n\n" + "\n".join(settled.notes)
            ),
            alternatives=[],
        )
        strategy = "prefilter_exact_reference"
        logger.info(
            "Invoice %s: %s settled it without a rerank call (score %.1f)",
            invoice.id,
            settled.order.name,
            settled.score,
        )
    else:
        verdict = await _rerank(extraction, candidates)
        strategy = "llm_rerank"

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
            strategy=strategy, confidence=verdict.confidence,
        )
        return

    await repo.update(
        invoice,
        status=InvoiceStatus.PENDING_REVIEW,
        matched_po_id=chosen.id,
        matched_po_name=chosen.name,
        confidence_score=verdict.confidence,
        # Recorded so these are countable later: if the corrected rate on
        # `prefilter_exact_reference` is not zero, the thresholds were wrong
        # and the data will say so rather than nobody noticing.
        match_strategy=strategy,
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
        company_id=invoice.company_id,
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
    """Ask the model to choose. Returns a validated verdict.

    Only the candidates the decision can turn on are described — see
    `_shortlist_for_prompt`. The full ranked list still reaches the audit blob.
    """
    shortlist = _shortlist_for_prompt(candidates)
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
                **c.order.for_prompt(item_limit=settings.MATCH_PROMPT_ITEM_CAP),
                "score": round(c.score, 1),
                # "v34 a0 d100 l0" rather than a five-key object: the same
                # numbers at a third of the characters, billed once per
                # candidate. The legend is in RERANK_PROMPT, billed once.
                "prefilter": " ".join(
                    f"{name[0]}{round(value)}" for name, value in c.breakdown.items()
                ),
            }
            for c in shortlist
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
        company_id=invoice.company_id,
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

    odoo = await odoo_for_invoice(db, invoice)
    order = await odoo.fetch_purchase_order(po_id)
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
            company_id=invoice.company_id,
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
            company_id=invoice.company_id,
        )

    await db.commit()
    return invoice
