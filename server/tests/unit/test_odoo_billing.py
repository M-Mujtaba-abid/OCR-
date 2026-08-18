"""The rules that decide how much a vendor gets paid.

Every function under test here is pure — it takes Odoo-shaped dicts and returns
an answer — which is precisely why they were extracted to module level in the
first place, following `match_recent_draft`. There is no network, no Odoo, and
no fixture: the arithmetic that turns a scanned invoice into a payable amount
should not need any of those to be proved correct.

The rows are real Odoo shapes, `[id, "display name"]` pairs and all, because
the normalisation of those pairs is part of what is being tested.
"""

from __future__ import annotations

from typing import Any

import pytest

import xmlrpc.client

from app.core.exceptions import OdooError, ReceiptNotPossibleError
from app.services.odoo_service import (
    StockQtyDialect,
    odoo_refusal,
    bill_display_name,
    bill_line_edits,
    choose_receipt_picking,
    classify_validate_action,
    group_writes,
    over_billed_lines,
    picking_move_writes,
    quantity_drift,
    receipt_blockers,
    remaining_to_bill,
    stock_qty_dialect,
)

ODOO_16 = frozenset({"quantity_done", "has_tracking"})
ODOO_17 = frozenset({"quantity", "picked", "has_tracking"})


def _po_line(
    line_id: int,
    *,
    ordered: float = 100.0,
    received: float = 0.0,
    invoiced: float = 0.0,
    product: str = "Widget A",
    display_type: Any = False,
) -> dict[str, Any]:
    return {
        "id": line_id,
        "name": f"{product} — as ordered",
        "product_id": [7, product],
        "product_qty": ordered,
        "qty_received": received,
        "qty_invoiced": invoiced,
        "display_type": display_type,
    }


def _move(
    move_id: int,
    *,
    po_line_id: int,
    picking_id: int = 900,
    demand: float = 100.0,
    tracking: Any = False,
    product: str = "Widget A",
) -> dict[str, Any]:
    return {
        "id": move_id,
        "picking_id": [picking_id, f"WH/IN/{picking_id}"],
        "product_id": [7, product],
        "purchase_line_id": [po_line_id, "P01690: Widget A"],
        "product_uom_qty": demand,
        "has_tracking": tracking,
        "state": "assigned",
    }


class TestStockQtyDialect:
    """Odoo 17 renamed the field and added a flag. Guessing wrong raises."""

    def test_odoo_17_uses_quantity_and_writes_picked(self) -> None:
        assert stock_qty_dialect(ODOO_17) == StockQtyDialect("quantity", True)

    def test_odoo_16_uses_quantity_done_and_does_not(self) -> None:
        assert stock_qty_dialect(ODOO_16) == StockQtyDialect("quantity_done", False)

    def test_neither_field_refuses_rather_than_guessing(self) -> None:
        """A database with no recognisable dialect must not be written to."""
        with pytest.raises(OdooError):
            stock_qty_dialect(frozenset({"has_tracking"}))


class TestOdooRefusal:
    """Telling "Odoo said no" apart from "Odoo broke".

    Measured against a real Odoo 17: a blocked goods receipt came back as fault
    code 2 with two clean sentences and no traceback, while a bad field name
    came back as code 1 with a full traceback carrying the deployment's own
    paths. The two must not be reported the same way.
    """

    def test_a_user_error_is_passed_through_as_written(self) -> None:
        """Odoo wrote this for a person. It is the whole value of the 409."""
        fault = xmlrpc.client.Fault(
            2,
            "This transfer cannot be validated because the quality check has "
            "not been successfully completed for: Banana.\n"
            "Please complete and pass the quality inspection first.",
        )

        message = odoo_refusal(fault)

        assert message is not None
        assert "quality check" in message
        # Joined into one sentence: the destination is a toast, not a page.
        assert "\n" not in message

    def test_a_traceback_is_never_passed_through(self) -> None:
        """Fault code 1 carries the database name and internal model paths."""
        fault = xmlrpc.client.Fault(
            1,
            'Traceback (most recent call last):\n'
            '  File "/var/odoo/staging-acme.example/src/odoo/models.py", line 1\n'
            "ValueError: Invalid field 'x' on model 'purchase.order.line'",
        )

        assert odoo_refusal(fault) is None

    def test_an_empty_user_error_is_not_a_message(self) -> None:
        """Better a generic 502 than a 409 with nothing in it."""
        assert odoo_refusal(xmlrpc.client.Fault(2, "   \n  ")) is None

    def test_a_pathological_message_is_capped(self) -> None:
        assert len(odoo_refusal(xmlrpc.client.Fault(2, "x" * 5000)) or "") == 400


class TestRemainingToBill:
    def test_nothing_billed_yet_leaves_the_whole_order(self) -> None:
        assert remaining_to_bill(_po_line(1, ordered=100)) == 100.0

    def test_a_half_billed_line_leaves_the_other_half(self) -> None:
        """The case this whole feature exists for: 100 ordered, 50 billed."""
        assert remaining_to_bill(_po_line(1, ordered=100, invoiced=50)) == 50.0

    def test_received_is_irrelevant_to_the_answer(self) -> None:
        """Billing ahead of delivery is legitimate — a prepayment, a service.

        `received` is shown to the reviewer instead, which is where that
        judgement belongs.
        """
        assert remaining_to_bill(_po_line(1, ordered=100, received=0)) == 100.0
        assert remaining_to_bill(_po_line(1, ordered=100, received=100)) == 100.0

    def test_a_credit_note_cannot_produce_a_negative_remaining(self) -> None:
        """`qty_invoiced` can exceed `product_qty`. A negative would read as
        a licence to bill."""
        assert remaining_to_bill(_po_line(1, ordered=15, invoiced=16)) == 0.0

    def test_a_section_heading_is_not_billable(self) -> None:
        assert remaining_to_bill(_po_line(1, display_type="line_section")) == 0.0


class TestOverBilling:
    def test_billing_exactly_what_is_left_is_allowed(self) -> None:
        lines = [_po_line(1, ordered=100, invoiced=50)]
        assert over_billed_lines(lines, {1: 50.0}) == []

    def test_one_line_over_names_the_product_and_both_numbers(self) -> None:
        lines = [_po_line(1, ordered=100, invoiced=50)]

        [over] = over_billed_lines(lines, {1: 60.0})

        assert over.label == "Widget A"
        assert over.requested == 60.0
        assert over.remaining == 50.0

    def test_every_offending_line_is_returned_not_just_the_first(self) -> None:
        """A reviewer correcting a three-line invoice should see three numbers,
        not be sent round three times."""
        lines = [
            _po_line(1, ordered=10, product="Widget A"),
            _po_line(2, ordered=10, product="Widget B"),
            _po_line(3, ordered=10, product="Widget C"),
        ]

        over = over_billed_lines(lines, {1: 11.0, 2: 5.0, 3: 12.0})

        assert {o.label for o in over} == {"Widget A", "Widget C"}

    def test_floating_point_noise_is_not_over_billing(self) -> None:
        lines = [_po_line(1, ordered=8.0)]
        assert over_billed_lines(lines, {1: 8.0000001}) == []

    def test_a_line_from_another_order_is_left_to_the_ownership_check(self) -> None:
        """Not this function's refusal to make — and silently over-billing a
        line it cannot see would be worse than saying nothing."""
        assert over_billed_lines([_po_line(1)], {999: 5.0}) == []


class TestChooseReceiptPicking:
    def test_the_oldest_covering_receipt_wins(self) -> None:
        """Oldest first, because that is the order a vendor delivers in: the
        original receipt before its backorder."""
        moves = [
            _move(1, po_line_id=10, picking_id=900),
            _move(2, po_line_id=11, picking_id=900),
            _move(3, po_line_id=10, picking_id=901),
            _move(4, po_line_id=11, picking_id=901),
        ]

        assert choose_receipt_picking(moves, {10, 11}, {900, 901}) == 900

    def test_an_internal_transfer_is_not_a_vendor_receipt(self) -> None:
        """A 2-step warehouse chains internal transfers off the same PO lines.
        Validating one of those is not receiving from the vendor."""
        moves = [
            _move(1, po_line_id=10, picking_id=800),  # internal
            _move(2, po_line_id=10, picking_id=900),  # incoming
        ]

        assert choose_receipt_picking(moves, {10}, {900}) == 900

    def test_an_invoice_spanning_two_receipts_is_refused(self) -> None:
        """Deciding which half of a paper invoice belongs to which receipt is a
        judgement this code does not have the information to make."""
        moves = [
            _move(1, po_line_id=10, picking_id=900),
            _move(2, po_line_id=11, picking_id=901),
        ]

        with pytest.raises(ReceiptNotPossibleError):
            choose_receipt_picking(moves, {10, 11}, {900, 901})


class TestReceiptBlockers:
    def test_a_clean_partial_receipt_has_no_blockers(self) -> None:
        assert receipt_blockers([_move(1, po_line_id=10, demand=100)], {10: 50.0}) == []

    def test_a_tracked_product_is_refused_before_anything_is_written(self) -> None:
        """`button_validate` would raise deep inside the wizard — AFTER the
        quantities were written."""
        moves = [_move(1, po_line_id=10, tracking="serial")]

        [problem] = receipt_blockers(moves, {10: 50.0})

        assert "lot/serial" in problem

    def test_receiving_more_than_the_receipt_expects_is_refused(self) -> None:
        moves = [_move(1, po_line_id=10, demand=40)]

        [problem] = receipt_blockers(moves, {10: 50.0})

        assert "50" in problem and "40" in problem

    def test_a_line_with_no_open_move_is_named(self) -> None:
        assert "11" in receipt_blockers([_move(1, po_line_id=10)], {11: 5.0})[0]


class TestPickingMoveWrites:
    def test_odoo_17_writes_quantity_and_picked(self) -> None:
        moves = [_move(1, po_line_id=10)]

        writes = picking_move_writes(moves, {10: 50.0}, StockQtyDialect("quantity", True))

        assert writes == {1: {"quantity": 50.0, "picked": True}}

    def test_odoo_16_writes_quantity_done_and_no_flag(self) -> None:
        moves = [_move(1, po_line_id=10)]

        writes = picking_move_writes(
            moves, {10: 50.0}, StockQtyDialect("quantity_done", False)
        )

        assert writes == {1: {"quantity_done": 50.0}}

    def test_an_unapproved_move_is_explicitly_zeroed(self) -> None:
        """The most important assertion in this file.

        Odoo 17 pre-fills `quantity` from the reservation, so a move left
        untouched is a move that gets received IN FULL. Without the explicit
        zero, billing 50 of 100 quietly receives all 100.
        """
        moves = [_move(1, po_line_id=10), _move(2, po_line_id=11)]

        writes = picking_move_writes(
            moves, {10: 50.0}, StockQtyDialect("quantity", True)
        )

        assert writes[2] == {"quantity": 0.0, "picked": False}


class TestQuantityDrift:
    def test_quantities_odoo_stored_as_written_are_no_drift(self) -> None:
        intended = {1: {"quantity": 50.0}}
        read_back = [{"id": 1, "quantity": 50.0}]

        assert quantity_drift(read_back, intended, qty_field="quantity") == []

    def test_a_uom_conversion_shows_up_as_drift(self) -> None:
        """A dozen written as 50 comes back as 600. This is the check that turns
        that into a refusal instead of a shipment."""
        intended = {1: {"quantity": 50.0}}
        read_back = [{"id": 1, "quantity": 600.0}]

        [drift] = quantity_drift(read_back, intended, qty_field="quantity")

        assert "50" in drift and "600" in drift


class TestClassifyValidateAction:
    def test_true_means_it_validated(self) -> None:
        assert classify_validate_action(True) == "done"

    def test_a_reception_report_is_not_a_wizard(self) -> None:
        """`button_validate` returns an ordinary action dict when the reception
        report is enabled, so "a dict came back" does not mean "it stopped"."""
        action = {"type": "ir.actions.act_window", "res_model": "report.stock.report"}

        assert classify_validate_action(action) == "done"

    def test_the_backorder_wizard_is_recognised(self) -> None:
        action = {"res_model": "stock.backorder.confirmation"}

        assert classify_validate_action(action) == "backorder"

    def test_the_immediate_transfer_wizard_is_recognised(self) -> None:
        """Its `process()` would receive the ENTIRE demand, so the caller aborts
        on this rather than answering it."""
        action = {"res_model": "stock.immediate.transfer"}

        assert classify_validate_action(action) == "immediate"


class TestBillLineEdits:
    def _bill_line(self, line_id: int, po_line_id: int, quantity: float) -> dict[str, Any]:
        return {
            "id": line_id,
            "purchase_line_id": [po_line_id, "P01690: Widget A"],
            "quantity": quantity,
            "product_id": [7, "Widget A"],
            "name": "Widget A",
        }

    def test_a_bill_already_at_the_right_quantity_needs_no_edit(self) -> None:
        """Odoo's default bill-control policy bills what was received, so after
        a partial receipt the trim is a no-op."""
        lines = [self._bill_line(1, 10, 50.0)]

        assert bill_line_edits(lines, {10: 50.0}) == []

    def test_an_over_billed_line_is_written_down(self) -> None:
        """The 'on ordered quantities' policy bills all 100. This is the trim
        that makes both policies converge."""
        lines = [self._bill_line(1, 10, 100.0)]

        assert bill_line_edits(lines, {10: 50.0}) == [(1, 1, {"quantity": 50.0})]

    def test_a_line_the_reviewer_left_off_is_deleted_not_zeroed(self) -> None:
        """A bill line for 0 units posts a 0.00 row, and Odoo's `qty_invoiced`
        would count a line saying nothing was billed."""
        lines = [self._bill_line(1, 10, 100.0), self._bill_line(2, 11, 20.0)]

        assert (2, 2, 0) in bill_line_edits(lines, {10: 100.0})

    def test_a_line_odoo_added_itself_is_left_alone(self) -> None:
        """No `purchase_line_id` means a section, a note, or something Odoo's
        own logic put there. Not ours to trim."""
        lines = [{"id": 3, "purchase_line_id": False, "quantity": 1.0}]

        assert bill_line_edits(lines, {10: 50.0}) == []


class TestBillDisplayName:
    def test_a_draft_bill_shows_the_vendors_own_number(self) -> None:
        """Odoo numbers a bill at post time. Echoing `name` for a draft would
        put a bare solidus on the review screen."""
        assert bill_display_name("/", "INV-4471", 7788) == "INV-4471 (draft #7788)"

    def test_a_posted_bill_shows_odoos_sequence(self) -> None:
        assert bill_display_name("BILL/2026/08/0031", "INV-4471", 7788) == (
            "BILL/2026/08/0031"
        )

    def test_a_draft_with_no_reference_still_reads_as_something(self) -> None:
        assert bill_display_name("/", None, 7788) == "Draft bill #7788"


class TestGroupWrites:
    def test_identical_values_collapse_into_one_call(self) -> None:
        """Forty moves on a picking are almost always two groups — the approved
        quantity and zero."""
        writes = {
            1: {"quantity": 50.0, "picked": True},
            2: {"quantity": 0.0, "picked": False},
            3: {"quantity": 0.0, "picked": False},
        }

        grouped = group_writes(writes)

        assert len(grouped) == 2
        assert ([2, 3], {"quantity": 0.0, "picked": False}) in grouped
