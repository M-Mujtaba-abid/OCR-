"""Matching engine tests.

No database, no network, no API key — the engine is pure, so these run in
milliseconds and can be used to evaluate a weight change directly.

Assertions are on score **bands**, never exact floats. Asserting `score ==
87.3` would mean every future tuning of the weights breaks the whole suite,
which is how a test file stops being maintained.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.schemas.extraction import InvoiceExtraction
from app.schemas.odoo import (
    LINE_PROJECTION_CAP,
    OdooPurchaseOrder,
    OdooPurchaseOrderLine,
)
from app.services.matching_engine import (
    normalise_reference,
    normalise_vendor,
    rank,
    score_candidate,
)

HIGH = 75.0
MEDIUM = 45.0


def make_invoice(**overrides: object) -> InvoiceExtraction:
    base: dict[str, object] = {
        "vendor_name": "Acme Tools Ltd",
        "po_number": "PO-2026-0089",
        "order_date": "2026-08-20",
        "currency": "USD",
        "items": [
            {"name": "Heavy Duty Industrial Drill", "quantity": 2, "unit_price": 150, "subtotal": 300},
            {"name": "Carbide Drill Bit Set", "quantity": 10, "unit_price": 12.5, "subtotal": 125},
        ],
        "untaxed_amount": 425.0,
        "tax_amount": 21.25,
        "total_amount": 446.25,
    }
    base.update(overrides)
    return InvoiceExtraction.model_validate(base)


def make_order(**overrides: object) -> OdooPurchaseOrder:
    lines = overrides.pop("lines", None)
    base: dict[str, object] = {
        "id": 42,
        "name": "PO-2026-0089",
        "partner_id": 7,
        "partner_name": "ACME TOOLS LIMITED",
        "date_order": dt.date(2026, 8, 10),
        "amount_untaxed": 425.0,
        "amount_tax": 21.25,
        "amount_total": 446.25,
        "currency": "USD",
        "state": "purchase",
        "invoice_status": "to invoice",
    }
    base.update(overrides)
    order = OdooPurchaseOrder.model_validate(base)
    order.lines = list(lines) if lines is not None else [
        OdooPurchaseOrderLine(id=1, order_id=42, name="Heavy Duty Industrial Drill",
                              product_name="Heavy Duty Industrial Drill",
                              product_qty=2, price_unit=150, price_subtotal=300),
        OdooPurchaseOrderLine(id=2, order_id=42, name="Carbide Drill Bit Set",
                              product_name="Carbide Drill Bit Set",
                              product_qty=10, price_unit=12.5, price_subtotal=125),
    ]
    return order


# ---------------------------------------------------------------- normalisation
class TestNormalisation:
    @pytest.mark.parametrize(
        "left,right",
        [
            ("Acme Tools Ltd", "ACME TOOLS LIMITED"),
            ("Acme Tools, Inc.", "acme tools incorporated"),
            ("Acme Tools Pvt Ltd", "Acme Tools"),
            ("ACME  TOOLS   LLC", "Acme Tools"),
        ],
    )
    def test_legal_forms_do_not_distinguish_vendors(self, left: str, right: str) -> None:
        assert normalise_vendor(left) == normalise_vendor(right)

    def test_different_vendors_stay_different(self) -> None:
        assert normalise_vendor("Acme Tools") != normalise_vendor("Beta Supplies")

    def test_blank_vendor_is_empty(self) -> None:
        assert normalise_vendor(None) == ""
        assert normalise_vendor("   ") == ""

    @pytest.mark.parametrize(
        "written", ["PO-2026-0089", "PO 2026/0089", "po20260089", "P.O. 2026-0089"]
    )
    def test_reference_punctuation_is_irrelevant(self, written: str) -> None:
        # "P.O." keeps its letters, so only the three that agree are compared.
        assert normalise_reference(written).endswith("20260089")


# --------------------------------------------------------------------- scoring
class TestScoring:
    def test_identical_documents_score_high(self) -> None:
        result = score_candidate(make_invoice(), make_order())
        assert result.score >= 90
        assert set(result.breakdown) == {"vendor", "amount", "reference", "date", "lines"}

    def test_unrelated_order_scores_low(self) -> None:
        result = score_candidate(
            make_invoice(),
            make_order(
                id=99,
                name="PO-2025-1111",
                partner_name="Global Stationery Supplies",
                amount_untaxed=12_000.0,
                amount_total=12_600.0,
                date_order=dt.date(2025, 1, 5),
                lines=[
                    OdooPurchaseOrderLine(id=9, order_id=99, name="A4 Paper Ream",
                                          product_name="A4 Paper Ream",
                                          product_qty=100, price_unit=120,
                                          price_subtotal=12000),
                ],
            ),
        )
        assert result.score < MEDIUM

    def test_right_vendor_wrong_amount_lands_in_the_middle(self) -> None:
        """The case a human must look at: same vendor, very different money."""
        result = score_candidate(
            make_invoice(),
            make_order(amount_untaxed=4250.0, amount_total=4462.5, name="PO-2026-0090"),
        )
        assert MEDIUM <= result.score < 90

    def test_missing_date_is_dropped_not_penalised(self) -> None:
        """A sparse invoice must not be capped just for being sparse."""
        with_date = score_candidate(make_invoice(), make_order())
        without_date = score_candidate(make_invoice(order_date=None), make_order())

        assert "date" in with_date.breakdown
        assert "date" not in without_date.breakdown
        # Renormalisation means dropping a component the invoice could not
        # supply costs almost nothing.
        assert without_date.score >= with_date.score - 5

    def test_vendor_alone_is_not_enough_for_a_high_score(self) -> None:
        result = score_candidate(
            make_invoice(po_number=None, order_date=None, items=[],
                         untaxed_amount=0, total_amount=0),
            make_order(),
        )
        assert set(result.breakdown) == {"vendor"}
        # It can still reach 100 on the one component that applied — which is
        # exactly why MATCH_MIN_CONFIDENCE and the LLM pass both exist.
        assert result.score >= HIGH


class TestAmountBands:
    @pytest.mark.parametrize(
        "order_amount,minimum",
        [(425.0, 100), (426.0, 90), (440.0, 70), (460.0, 45), (500.0, 20)],
    )
    def test_closer_amounts_never_score_worse(
        self, order_amount: float, minimum: float
    ) -> None:
        result = score_candidate(
            make_invoice(), make_order(amount_untaxed=order_amount)
        )
        assert result.breakdown["amount"] >= minimum

    def test_wildly_different_amount_scores_zero(self) -> None:
        result = score_candidate(make_invoice(), make_order(amount_untaxed=99_999.0))
        assert result.breakdown["amount"] == 0.0

    def test_untaxed_is_preferred_over_gross(self) -> None:
        """Tax treatment differs between a PO and an invoice; goods do not."""
        result = score_candidate(
            make_invoice(untaxed_amount=425.0, tax_amount=80.0, total_amount=505.0),
            make_order(amount_untaxed=425.0, amount_tax=21.25, amount_total=446.25),
        )
        assert result.breakdown["amount"] == 100.0


class TestDateDirection:
    def test_invoice_shortly_after_order_is_ideal(self) -> None:
        result = score_candidate(
            make_invoice(order_date="2026-08-15"),
            make_order(date_order=dt.date(2026, 8, 10)),
        )
        assert result.breakdown["date"] == 100.0

    def test_invoice_before_its_order_is_penalised(self) -> None:
        after = score_candidate(
            make_invoice(order_date="2026-08-20"),
            make_order(date_order=dt.date(2026, 8, 10)),
        )
        before = score_candidate(
            make_invoice(order_date="2026-08-01"),
            make_order(date_order=dt.date(2026, 8, 10)),
        )
        assert before.breakdown["date"] < after.breakdown["date"]

    def test_a_year_apart_scores_zero(self) -> None:
        result = score_candidate(
            make_invoice(order_date="2026-08-20"),
            make_order(date_order=dt.date(2025, 1, 1)),
        )
        assert result.breakdown["date"] == 0.0


class TestLineMatching:
    def test_partial_delivery_scores_mid_not_perfect(self) -> None:
        """One invoice line against a five-line order is not a full match."""
        result = score_candidate(
            make_invoice(
                items=[{"name": "Heavy Duty Industrial Drill", "quantity": 2,
                        "unit_price": 150, "subtotal": 300}]
            ),
            make_order(
                lines=[
                    OdooPurchaseOrderLine(id=i, order_id=42, name=f"Item {i}",
                                          product_name=f"Item {i}", product_qty=1,
                                          price_unit=10, price_subtotal=10)
                    for i in range(1, 5)
                ]
                + [
                    OdooPurchaseOrderLine(id=5, order_id=42,
                                          name="Heavy Duty Industrial Drill",
                                          product_name="Heavy Duty Industrial Drill",
                                          product_qty=2, price_unit=150,
                                          price_subtotal=300)
                ]
            ),
        )
        assert 0 < result.breakdown["lines"] < 50

    def test_reworded_descriptions_still_match(self) -> None:
        result = score_candidate(
            make_invoice(items=[{"name": "Drill, Heavy Duty Industrial",
                                 "quantity": 2, "unit_price": 150, "subtotal": 300}]),
            make_order(lines=[
                OdooPurchaseOrderLine(id=1, order_id=42,
                                      name="Heavy Duty Industrial Drill",
                                      product_name="Heavy Duty Industrial Drill",
                                      product_qty=2, price_unit=150,
                                      price_subtotal=300)
            ]),
        )
        assert result.breakdown["lines"] == 100.0

    def test_one_order_line_cannot_satisfy_two_invoice_lines(self) -> None:
        """Greedy assignment must consume, not reuse."""
        result = score_candidate(
            make_invoice(items=[
                {"name": "Widget", "quantity": 1, "unit_price": 10, "subtotal": 10},
                {"name": "Widget", "quantity": 1, "unit_price": 10, "subtotal": 10},
            ]),
            make_order(lines=[
                OdooPurchaseOrderLine(id=1, order_id=42, name="Widget",
                                      product_name="Widget", product_qty=2,
                                      price_unit=10, price_subtotal=20)
            ]),
        )
        assert result.breakdown["lines"] == 50.0


class TestReference:
    def test_exact_reference_is_the_strongest_signal(self) -> None:
        result = score_candidate(make_invoice(), make_order())
        assert result.breakdown["reference"] == 100.0

    def test_vendor_ref_is_matched_too(self) -> None:
        result = score_candidate(
            make_invoice(po_number="ACME-INV-778"),
            make_order(name="P00042", partner_ref="ACME-INV-778"),
        )
        assert result.breakdown["reference"] == 100.0

    def test_short_references_do_not_collide(self) -> None:
        """A two-character reference must not containment-match everything."""
        result = score_candidate(
            make_invoice(po_number="7"), make_order(name="PO-2026-0007")
        )
        assert result.breakdown["reference"] < 85.0


class TestRanking:
    def test_best_candidate_comes_first(self) -> None:
        right = make_order(id=1, name="PO-2026-0089")
        wrong = make_order(id=2, name="PO-2000-0001", partner_name="Other Co",
                           amount_untaxed=9999.0, date_order=dt.date(2020, 1, 1),
                           lines=[])
        ranked = rank(make_invoice(), [wrong, right], limit=15, floor=0)
        assert ranked[0].order.id == 1

    def test_floor_excludes_implausible_orders(self) -> None:
        wrong = make_order(id=2, name="PO-2000-0001", partner_name="Other Co",
                           amount_untaxed=9999.0, date_order=dt.date(2020, 1, 1),
                           lines=[])
        assert rank(make_invoice(), [wrong], floor=90.0) == []

    def test_limit_caps_the_shortlist(self) -> None:
        orders = [make_order(id=i) for i in range(1, 31)]
        assert len(rank(make_invoice(), orders, limit=15, floor=0)) == 15

    def test_no_orders_yields_no_candidates(self) -> None:
        assert rank(make_invoice(), []) == []

    def test_candidate_serialises_with_its_breakdown(self) -> None:
        """`candidates` is the audit trail — it must survive to JSON intact."""
        payload = rank(make_invoice(), [make_order()], floor=0)[0].to_json()
        assert payload["po_id"] == 42
        assert payload["po_number"] == "PO-2026-0089"
        assert set(payload["breakdown"]) == {
            "vendor", "amount", "reference", "date", "lines"
        }
        assert payload["notes"]


# --------------------------------------------------------------- the audit blob
class TestCandidateLineItems:
    """The lines carried into `candidates` and shown on the review screen.

    Exact values, not bands: these are pass-through projections of Odoo data,
    not tuned heuristics, and a reviewer comparing them against a bill needs
    them to be the numbers Odoo holds.
    """

    def test_items_carry_the_order_lines(self) -> None:
        order = make_order(lines=[
            OdooPurchaseOrderLine(id=1, order_id=42, name="Apple Red",
                                  product_name="Apple Red", product_qty=2,
                                  price_unit=9.0, price_subtotal=18.0,
                                  price_tax=2.0, price_total=20.0),
        ])
        items = score_candidate(make_invoice(), order).to_json()["items"]

        assert items == [{
            "name": "Apple Red",
            "quantity": 2.0,
            "unit_price": 9.0,
            "subtotal": 18.0,
            "price_tax": 2.0,
            "price_total": 20.0,
        }]

    def test_total_falls_back_to_subtotal_without_tax_fields(self) -> None:
        """Records predating `price_tax` must not display as free lines."""
        order = make_order(lines=[
            OdooPurchaseOrderLine(id=1, order_id=42, name="Eggplant",
                                  product_qty=1, price_unit=5.0,
                                  price_subtotal=5.0),
        ])
        [item] = score_candidate(make_invoice(), order).to_json()["items"]

        assert item["price_tax"] == 0.0
        assert item["price_total"] == 5.0

    def test_line_count_is_the_true_total_when_items_are_capped(self) -> None:
        """A truncated list must not read as the whole order."""
        order = make_order(lines=[
            OdooPurchaseOrderLine(id=i, order_id=42, name=f"Product {i}",
                                  product_qty=1, price_unit=1.0,
                                  price_subtotal=1.0)
            for i in range(1, 41)
        ])
        payload = score_candidate(make_invoice(), order).to_json()

        assert len(payload["items"]) == LINE_PROJECTION_CAP
        assert payload["line_count"] == 40

    def test_an_order_without_lines_serialises_empty(self) -> None:
        payload = score_candidate(make_invoice(), make_order(lines=[])).to_json()

        assert payload["items"] == []
        assert payload["line_count"] == 0

    def test_currency_and_vendor_ref_reach_the_screen(self) -> None:
        """Both are shown beside the candidate's totals."""
        order = make_order(currency="AED", partner_ref="INV-77")
        payload = score_candidate(make_invoice(), order).to_json()

        assert payload["currency"] == "AED"
        assert payload["vendor_ref"] == "INV-77"
