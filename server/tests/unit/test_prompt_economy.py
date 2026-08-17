"""What the model is shown, and when it is asked at all.

The rerank call is ~85% of what an invoice costs to process, and almost all of
that is the candidate list. These guard the two decisions that shrink it
without changing which order gets matched: describing only the candidates the
decision can turn on, and not asking at all when the vendor quoted the order's
reference outright.

The distinction under test throughout: the SCREEN keeps every candidate — that
is what makes a wrong match arguable afterwards — while the PROMPT keeps only
the ones worth paying for.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.config import settings
from app.schemas.odoo import OdooPurchaseOrder
from app.services import matching_engine
from app.services.match_service import _beyond_argument, _shortlist_for_prompt


def make_candidate(
    score: float,
    *,
    po_id: int = 1,
    reference: float | None = None,
    invoice_status: str = "to invoice",
) -> matching_engine.ScoredCandidate:
    order = OdooPurchaseOrder.model_validate(
        {
            "id": po_id,
            "name": f"P0{po_id}",
            "partner_name": "Berry Mount Vegetables And Fruit Trading Llc",
            "partner_ref": None,
            "date_order": dt.date(2026, 7, 24),
            "amount_untaxed": 50000.0,
            "amount_total": 52500.0,
            "currency": "AED",
            "state": "purchase",
            "invoice_status": invoice_status,
        }
    )
    order.lines = []
    breakdown = {"vendor": 100.0, "amount": 100.0, "date": 100.0, "lines": 100.0}
    if reference is not None:
        breakdown["reference"] = reference
    return matching_engine.ScoredCandidate(
        order=order, score=score, breakdown=breakdown, notes=["a note"]
    )


class TestShortlistForPrompt:
    def test_a_tied_cluster_is_kept_whole(self) -> None:
        """Where nothing separates the top, the full spend is the right one."""
        candidates = [make_candidate(s, po_id=i) for i, s in enumerate([42, 42, 42, 41])]

        assert len(_shortlist_for_prompt(candidates)) == 4

    def test_a_distant_tail_is_not_described(self) -> None:
        """The tail is never picked — it is only billed for."""
        scores = [100.0, 61.0, 48.0, 37.0, 37.0, 36.0, 36.0, 35.0]
        candidates = [make_candidate(s, po_id=i) for i, s in enumerate(scores)]

        kept = _shortlist_for_prompt(candidates)

        assert len(kept) == settings.MATCH_PROMPT_MIN
        assert kept[0].score == 100.0

    def test_never_fewer_than_the_floor(self) -> None:
        """A shortlist of one is a decision the model was never offered."""
        candidates = [make_candidate(s, po_id=i) for i, s in enumerate([100.0, 40.0])]

        assert len(_shortlist_for_prompt(candidates)) == 2  # all there is

    def test_the_margin_is_configurable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MATCH_PROMPT_MARGIN", 100.0)
        monkeypatch.setattr(settings, "MATCH_PROMPT_MIN", 1)
        scores = [100.0, 61.0, 48.0, 37.0, 36.0, 35.0]
        candidates = [make_candidate(s, po_id=i) for i, s in enumerate(scores)]

        assert len(_shortlist_for_prompt(candidates)) == len(scores)


class TestBeyondArgument:
    def test_an_exact_reference_with_daylight_settles_it(self) -> None:
        """The vendor quoted the order's number — the model cannot add to that."""
        candidates = [
            make_candidate(100.0, po_id=1, reference=100.0),
            make_candidate(62.0, po_id=2),
        ]

        settled = _beyond_argument(candidates)

        assert settled is not None
        assert settled.order.id == 1

    def test_a_resemblance_is_not_a_reference(self) -> None:
        """85 is the containment hit — a resemblance, not a quoted number."""
        candidates = [
            make_candidate(96.0, po_id=1, reference=85.0),
            make_candidate(50.0, po_id=2),
        ]

        assert _beyond_argument(candidates) is None

    def test_a_near_twin_always_gets_the_model(self) -> None:
        """Two orders this close is exactly when a second opinion is worth $0.008."""
        candidates = [
            make_candidate(100.0, po_id=1, reference=100.0),
            make_candidate(87.0, po_id=2),
        ]

        assert _beyond_argument(candidates) is None

    def test_an_already_billed_order_never_settles_it(self) -> None:
        """The duplicate-bill case is the one that most needs looking at."""
        candidates = [
            make_candidate(100.0, po_id=1, reference=100.0, invoice_status="invoiced"),
            make_candidate(40.0, po_id=2),
        ]

        assert _beyond_argument(candidates) is None

    def test_no_reference_no_fast_path(self) -> None:
        """A perfect score on vendor, amount and date is still not a statement."""
        candidates = [make_candidate(96.0, po_id=1), make_candidate(40.0, po_id=2)]

        assert _beyond_argument(candidates) is None

    def test_a_lone_candidate_can_settle_it(self) -> None:
        """Nothing to be closer than is not a reason to pay for a second opinion."""
        candidates = [make_candidate(100.0, po_id=1, reference=100.0)]

        assert _beyond_argument(candidates) is not None

    def test_the_fast_path_can_be_switched_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment that wants every match reasoned about can have that."""
        monkeypatch.setattr(settings, "MATCH_AUTO_ACCEPT_SCORE", 101.0)
        candidates = [
            make_candidate(100.0, po_id=1, reference=100.0),
            make_candidate(40.0, po_id=2),
        ]

        assert _beyond_argument(candidates) is None


class TestPromptProjection:
    def test_absent_fields_are_omitted_not_sent_as_null(self) -> None:
        """`"vendor_ref": null` is billed on every candidate to say nothing."""
        order = make_candidate(100.0).order
        projection = order.for_prompt()

        assert "vendor_ref" not in projection
        assert projection["po_number"] == "P01"

    def test_items_are_capped_for_the_prompt(self) -> None:
        """The line-item SCORE already saw every line; these rows are evidence."""
        from app.schemas.odoo import OdooPurchaseOrderLine

        order = make_candidate(100.0).order
        order.lines = [
            OdooPurchaseOrderLine(id=i, order_id=1, name=f"Product {i}", product_qty=1)
            for i in range(1, 31)
        ]

        assert len(order.for_prompt(item_limit=12)["items"]) == 12
        assert len(order.line_items()) == 25  # the audit blob keeps more
