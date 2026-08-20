"""Turning a matched invoice into a vendor bill, and refusing to.

The case every test here is shaped around: 100 pieces are ordered on one
purchase order, the vendor delivers and bills 50 now and 50 next month, and each
paper invoice becomes its own bill against the same order. That makes "this
order already has a bill" the normal state rather than a duplicate, and it is
why the guard keys on the vendor's own invoice number instead.

No network. Odoo is faked at the `odoo_service` METHOD boundary, never at the
XML-RPC transport, so what is under test is the judgement.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from app.services.odoo_service import OdooCredentials

from app.core.exceptions import InvoiceNotReadyError, OverBilledError
from app.schemas.extraction import ExtractedLineItem
from app.schemas.invoice import AttachmentStatus, BillOutcome
from app.schemas.odoo import (
    OdooAttachment,
    OdooCreatedBill,
    OdooExistingBill,
    OdooPurchaseOrder,
    OdooPurchaseOrderLine,
    OdooReceiptResult,
)
from app.services import bill_creator_service as bcs
from app.services import source_document

PO_ID = 1690


def _po_line(
    line_id: int,
    *,
    product: str = "Widget A",
    ordered: float = 100.0,
    received: float = 0.0,
    invoiced: float = 0.0,
    price: float = 10.0,
    display_type: str | None = None,
) -> OdooPurchaseOrderLine:
    return OdooPurchaseOrderLine(
        id=line_id,
        order_id=PO_ID,
        name=f"{product} — as ordered",
        product_id=line_id + 100,
        product_name=product,
        product_qty=ordered,
        qty_received=received,
        qty_invoiced=invoiced,
        price_unit=price,
        display_type=display_type,
    )


def _order(*lines: OdooPurchaseOrderLine, state: str = "purchase") -> OdooPurchaseOrder:
    return OdooPurchaseOrder(
        id=PO_ID,
        name="P01690",
        partner_id=7,
        partner_name="Acme Tools Ltd",
        state=state,
        currency="AED",
        lines=list(lines) or [_po_line(10)],
    )


def _item(name: str, quantity: float = 50.0, price: float = 10.0) -> ExtractedLineItem:
    return ExtractedLineItem(
        name=name,
        product_code=None,
        uom=None,
        quantity=quantity,
        unit_price=price,
        subtotal=quantity * price,
        tax=0.0,
    )


@pytest.fixture
def odoo(monkeypatch: pytest.MonkeyPatch):
    """Stub the order read, the bill search, the receipt and the create."""
    state: dict[str, Any] = {
        "order": _order(_po_line(10)),
        "existing": [],
        "created": {},
        "received": {},
        "fetched": 0,
    }

    state["attached"] = None

    async def fake_fetch_po(po_id: int):
        state["fetched"] += 1
        return state["order"] if po_id == PO_ID else None

    async def fake_find_bills(*, partner_id: int, ref: str) -> list[OdooExistingBill]:
        return [b for b in state["existing"] if b.ref == ref]

    async def fake_receive(*, po_id: int, quantities: dict[int, float]):
        state["received"] = dict(quantities)
        return OdooReceiptResult(
            picking_id=900,
            picking_name="WH/IN/00042",
            backorder_ids=[901],
            backorder_names=["WH/IN/00043"],
            received=dict(quantities),
        )

    async def fake_create_bill(**kwargs: Any) -> OdooCreatedBill:
        state["created"] = kwargs
        attachment: OdooAttachment | None = kwargs.get("attachment")
        return OdooCreatedBill(
            id=7788,
            name="/",
            ref=kwargs.get("vendor_ref"),
            display_name=f"{kwargs.get('vendor_ref')} (draft #7788)",
            attachment_status="attached" if attachment else "none",
        )

    async def fake_attach(*, res_model: str, res_id: int, attachment: OdooAttachment):
        state["attached"] = (res_model, res_id, attachment.file_name)
        return "attached", 4242

    class _FakeOdoo:
        """ONE company's Odoo, faked at the method boundary.

        `attach_document` is stubbed even though the happy path does not reach
        it: the duplicate branch does, and an unstubbed one would try to reach
        the real Odoo from a unit test — and write to it if it answered.
        """

        _credentials = OdooCredentials(
            base_url="https://odoo.test", database="db", username="u", api_key="k"
        )
        fetch_purchase_order = staticmethod(fake_fetch_po)
        find_vendor_bills = staticmethod(fake_find_bills)
        receive_purchase_order_lines = staticmethod(fake_receive)
        create_vendor_bill = staticmethod(fake_create_bill)
        attach_document = staticmethod(fake_attach)

    async def fake_resolve(_db: object, _invoice: object) -> _FakeOdoo:
        return _FakeOdoo()

    # The seam that replaced patching a module-level singleton: the code asks
    # the invoice which company's Odoo to use, so the test answers that.
    monkeypatch.setattr(bcs, "odoo_for_invoice", fake_resolve)
    monkeypatch.setattr(bcs, "MatchHistoryRepository", _FakeRepo)
    monkeypatch.setattr(bcs, "NotificationService", _FakeNotifier)
    # A company that gates nothing — no active approval chain — which is what
    # every test in this file is about. That the gate REFUSES when a chain is
    # running is proved against a real database in tests/test_approvals.py,
    # because the interesting part of it is rows and constraints.
    monkeypatch.setattr(bcs, "ApprovalService", _FakeApprovals)

    async def fake_download(_key: str) -> bytes:
        return b"%PDF-1.4 scanned invoice"

    # The document reader moved out of this module when the purchase-order
    # path started needing it too; patch it where it now reads storage.
    monkeypatch.setattr(source_document.storage, "download_file", fake_download)
    return state


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------
class TestRemainingToBill:
    def test_a_half_billed_line_leaves_the_other_half(self) -> None:
        assert bcs.remaining_to_bill(_po_line(10, ordered=100, invoiced=50)) == 50.0

    def test_delivery_does_not_limit_what_may_be_billed(self) -> None:
        """A prepayment or a service is billed before anything arrives."""
        assert bcs.remaining_to_bill(_po_line(10, ordered=100, received=0)) == 100.0

    def test_a_credit_note_cannot_produce_a_negative(self) -> None:
        assert bcs.remaining_to_bill(_po_line(10, ordered=15, invoiced=16)) == 0.0

    def test_a_section_heading_is_not_billable(self) -> None:
        line = _po_line(10, display_type="line_section")
        assert bcs.remaining_to_bill(line) == 0.0


class TestProposeMapping:
    def test_two_invoice_lines_cannot_both_claim_one_order_line(self) -> None:
        """The most important test in this file.

        Without one-to-one assignment, a single bill double-counts a quantity —
        and no per-line remaining check catches it, because each entry looks
        legal on its own.
        """
        po_lines = [_po_line(10, product="Lemon ليمون")]
        items = [_item("Lemon"), _item("Lemon")]

        pairs, unmatched = bcs.propose_mapping(items, po_lines)

        assert set(pairs) == {10}
        assert pairs[10].invoice_line_no == 1
        assert unmatched == [2]

    def test_differently_worded_descriptions_still_pair(self) -> None:
        """A catalogue and a vendor's invoice genuinely word goods differently.
        The floor is 75, shared with the matcher that got the reviewer here."""
        po_lines = [_po_line(10, product="Heavy Duty Industrial Drill")]

        pairs, unmatched = bcs.propose_mapping([_item("Drill, Heavy Duty 18V")], po_lines)

        assert set(pairs) == {10}
        assert not unmatched

    def test_a_mixed_script_name_pairs(self) -> None:
        """`normalise_vendor` is script-agnostic, and a regression is silent."""
        po_lines = [_po_line(10, product="Lemon ليمون")]

        pairs, _ = bcs.propose_mapping([_item("Lemon")], po_lines)

        assert set(pairs) == {10}

    def test_a_split_word_beside_a_second_script_falls_below_the_floor(self) -> None:
        """Measured at 64: "Egg Plant" is two tokens against "eggplant", and the
        Arabic half dilutes `token_set_ratio` further. It stays unmatched.

        Recorded rather than worked around. The line is still shown on the
        preview with a proposed quantity of zero, so the reviewer types the
        number in — which is the right outcome for a pairing the machine cannot
        make confidently, and far better than guessing at what gets paid. The
        floor is shared with `matching_engine`, so loosening it here to make
        this pass would silently change which orders get matched at all.
        """
        po_lines = [_po_line(10, product="Eggplant باذنجان")]

        pairs, unmatched = bcs.propose_mapping([_item("Egg Plant")], po_lines)

        assert pairs == {}
        assert unmatched == [1]

    def test_nothing_matching_is_reported_not_guessed(self) -> None:
        po_lines = [_po_line(10, product="Lemon ليمون")]

        pairs, unmatched = bcs.propose_mapping([_item("Cement, 50kg bag")], po_lines)

        assert pairs == {}
        assert unmatched == [1]

    def test_a_section_heading_is_never_a_candidate(self) -> None:
        po_lines = [
            _po_line(10, product="Fruit", display_type="line_section"),
            _po_line(11, product="Lemon"),
        ]

        pairs, _ = bcs.propose_mapping([_item("Lemon")], po_lines)

        assert set(pairs) == {11}


class TestClassifyDuplicate:
    def _bill(self, bill_id: int, **kw: Any) -> OdooExistingBill:
        return OdooExistingBill(id=bill_id, ref="INV-4471", **kw)

    def test_no_bills_is_not_a_duplicate(self) -> None:
        assert bcs.classify_duplicate([]) is None

    def test_an_unpaid_bill_is_reported_as_existing(self) -> None:
        found = bcs.classify_duplicate([self._bill(1, state="draft")])

        assert found is not None and found[1] is BillOutcome.BILL_EXISTS

    def test_a_paid_bill_outranks_an_unpaid_one(self) -> None:
        """The worst case is the one the reviewer must be told about."""
        bills = [
            self._bill(1, state="draft", payment_state="not_paid"),
            self._bill(2, state="posted", payment_state="paid"),
        ]

        found = bcs.classify_duplicate(bills)

        assert found is not None
        assert found[0].id == 2 and found[1] is BillOutcome.ALREADY_PAID

    def test_a_cancelled_bill_is_ignored_entirely(self) -> None:
        """Odoo's record that the bill was a mistake. Treating it as a duplicate
        would permanently block the correct one from ever being raised."""
        assert bcs.classify_duplicate([self._bill(1, state="cancel")]) is None


class TestCheckOverBilling:
    def test_billing_exactly_what_remains_is_allowed(self) -> None:
        lines = {10: _po_line(10, ordered=100, invoiced=50)}

        assert bcs.check_over_billing([{"po_line_id": 10, "quantity": 50.0}], lines) is None

    def test_the_message_names_the_line_and_both_numbers(self) -> None:
        lines = {10: _po_line(10, ordered=100, invoiced=50)}

        message = bcs.check_over_billing([{"po_line_id": 10, "quantity": 60.0}], lines)

        assert message is not None
        assert "Widget A" in message and "60" in message and "50" in message

    def test_one_line_sent_twice_is_summed_before_checking(self) -> None:
        """Each entry is within the remaining quantity; together they are not.
        A per-entry loop lets this through."""
        lines = {10: _po_line(10, ordered=100, invoiced=50)}
        approved = [
            {"po_line_id": 10, "quantity": 30.0},
            {"po_line_id": 10, "quantity": 30.0},
        ]

        assert bcs.check_over_billing(approved, lines) is not None

    def test_floating_point_noise_is_not_over_billing(self) -> None:
        lines = {10: _po_line(10, ordered=8.0)}

        assert bcs.check_over_billing(
            [{"po_line_id": 10, "quantity": 8.0000000001}], lines
        ) is None


class TestResolveInvoiceDate:
    def test_the_documents_own_date_wins(self) -> None:
        assert bcs.resolve_invoice_date(dt.date(2026, 7, 1), dt.date(2026, 8, 18)) == (
            dt.date(2026, 7, 1)
        )

    def test_an_unread_date_falls_back_to_today(self) -> None:
        assert bcs.resolve_invoice_date(None, dt.date(2026, 8, 18)) == dt.date(2026, 8, 18)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
class TestCreateBill:
    @pytest.mark.asyncio
    async def test_a_partial_bill_receives_and_bills_the_same_quantity(
        self, odoo
    ) -> None:
        """50 of 100: the receipt and the bill must agree, or Odoo bills what
        arrived rather than what the paper says."""
        odoo["order"] = _order(_po_line(10, ordered=100))

        _, outcome = await bcs.create_bill_for_invoice(
            _db(),
            invoice=_invoice(),
            po_id=PO_ID,
            ref="INV-4471",
            invoice_date=None,
            lines=[{"po_line_id": 10, "quantity": 50.0}],
            receive_goods=True,
            attach_document=True,
            reviewer_id=_uuid(),
        )

        assert outcome["status"] is BillOutcome.BILL_CREATED
        assert odoo["received"] == {10: 50.0}
        assert odoo["created"]["quantities"] == {10: 50.0}
        assert outcome["receipt_name"] == "WH/IN/00042"
        assert outcome["backorder_names"] == ["WH/IN/00043"]

    @pytest.mark.asyncio
    async def test_a_second_invoice_against_a_part_billed_order_is_allowed(
        self, odoo
    ) -> None:
        """The whole point. A purchase order that already carries a bill is the
        normal case, not a duplicate."""
        odoo["order"] = _order(_po_line(10, ordered=100, received=50, invoiced=50))

        _, outcome = await bcs.create_bill_for_invoice(
            _db(),
            invoice=_invoice(),
            po_id=PO_ID,
            ref="INV-5502",
            invoice_date=None,
            lines=[{"po_line_id": 10, "quantity": 50.0}],
            receive_goods=True,
            attach_document=False,
            reviewer_id=_uuid(),
        )

        assert outcome["status"] is BillOutcome.BILL_CREATED

    @pytest.mark.asyncio
    async def test_the_payload_carries_only_ids_and_quantities(self, odoo) -> None:
        """Odoo derives product, price and tax from `purchase_line_id`. This is
        what stops somebody helpfully adding the OCR'd price and overwriting an
        agreed one."""
        await bcs.create_bill_for_invoice(
            _db(),
            invoice=_invoice(),
            po_id=PO_ID,
            ref="INV-4471",
            invoice_date=None,
            lines=[{"po_line_id": 10, "quantity": 50.0}],
            receive_goods=False,
            attach_document=False,
            reviewer_id=_uuid(),
        )

        assert set(odoo["created"]["quantities"]) == {10}
        assert isinstance(odoo["created"]["quantities"][10], float)

    @pytest.mark.asyncio
    async def test_an_already_billed_invoice_is_refused_before_any_odoo_call(
        self, odoo
    ) -> None:
        """The ordering guarantee, not just the error: an impatient second click
        must not reach Odoo at all."""
        invoice = _invoice()
        invoice.pushed_to_odoo = True
        invoice.odoo_bill_id = 7788

        with pytest.raises(InvoiceNotReadyError) as exc:
            await bcs.create_bill_for_invoice(
                _db(),
                invoice=invoice,
                po_id=PO_ID,
                ref="INV-4471",
                invoice_date=None,
                lines=[{"po_line_id": 10, "quantity": 50.0}],
                receive_goods=True,
                attach_document=True,
                reviewer_id=_uuid(),
            )

        assert exc.value.code == "BILL_ALREADY_CREATED"
        assert odoo["fetched"] == 0

    @pytest.mark.asyncio
    async def test_billing_against_a_different_order_than_the_match_is_refused(
        self, odoo
    ) -> None:
        """Which order an invoice belongs to is `/confirm`'s decision, and
        changing it here would bypass the `was_corrected` record."""
        with pytest.raises(InvoiceNotReadyError) as exc:
            await bcs.create_bill_for_invoice(
                _db(),
                invoice=_invoice(),
                po_id=9999,
                ref="INV-4471",
                invoice_date=None,
                lines=[{"po_line_id": 10, "quantity": 50.0}],
                receive_goods=True,
                attach_document=True,
                reviewer_id=_uuid(),
            )

        assert exc.value.code == "PO_MISMATCH"

    @pytest.mark.asyncio
    async def test_a_draft_rfq_cannot_be_billed(self, odoo) -> None:
        odoo["order"] = _order(_po_line(10), state="draft")

        with pytest.raises(InvoiceNotReadyError) as exc:
            await bcs.create_bill_for_invoice(
                _db(),
                invoice=_invoice(),
                po_id=PO_ID,
                ref="INV-4471",
                invoice_date=None,
                lines=[{"po_line_id": 10, "quantity": 50.0}],
                receive_goods=True,
                attach_document=True,
                reviewer_id=_uuid(),
            )

        assert exc.value.code == "PO_NOT_CONFIRMED"

    @pytest.mark.asyncio
    async def test_a_line_from_another_order_is_refused_before_the_write(
        self, odoo
    ) -> None:
        with pytest.raises(InvoiceNotReadyError) as exc:
            await bcs.create_bill_for_invoice(
                _db(),
                invoice=_invoice(),
                po_id=PO_ID,
                ref="INV-4471",
                invoice_date=None,
                lines=[{"po_line_id": 4242, "quantity": 50.0}],
                receive_goods=True,
                attach_document=True,
                reviewer_id=_uuid(),
            )

        assert exc.value.code == "PO_LINE_MISMATCH"
        assert odoo["received"] == {}

    @pytest.mark.asyncio
    async def test_over_billing_is_refused_and_nothing_is_received(self, odoo) -> None:
        """The refusal has to land before `button_validate`, which cannot be
        undone."""
        odoo["order"] = _order(_po_line(10, ordered=100, invoiced=50))

        with pytest.raises(OverBilledError) as exc:
            await bcs.create_bill_for_invoice(
                _db(),
                invoice=_invoice(),
                po_id=PO_ID,
                ref="INV-4471",
                invoice_date=None,
                lines=[{"po_line_id": 10, "quantity": 60.0}],
                receive_goods=True,
                attach_document=True,
                reviewer_id=_uuid(),
            )

        assert "Widget A" in exc.value.message
        assert odoo["received"] == {}
        assert odoo["created"] == {}

    @pytest.mark.asyncio
    async def test_an_existing_paid_bill_answers_200_and_creates_nothing(
        self, odoo
    ) -> None:
        """A duplicate is a legitimate answer to "create this bill", not an
        error the client has to parse a message out of."""
        odoo["existing"] = [
            OdooExistingBill(
                id=555, ref="INV-4471", state="posted", payment_state="paid"
            )
        ]

        _, outcome = await bcs.create_bill_for_invoice(
            _db(),
            invoice=_invoice(),
            po_id=PO_ID,
            ref="INV-4471",
            invoice_date=None,
            lines=[{"po_line_id": 10, "quantity": 50.0}],
            receive_goods=True,
            attach_document=True,
            reviewer_id=_uuid(),
        )

        assert outcome["status"] is BillOutcome.ALREADY_PAID
        assert outcome["bill_id"] == 555
        assert odoo["created"] == {}
        assert odoo["received"] == {}

        # No second bill — but the document still goes onto the one that
        # exists. This branch is reached by the reviewer who clicked twice, or
        # whose first attempt created the bill and then failed, and those are
        # exactly the bills that used to end up with nothing attached and get
        # a PDF uploaded onto them by hand.
        assert odoo["attached"] == ("account.move", 555, "invoice.pdf")
        assert outcome["attachment_status"] is AttachmentStatus.ATTACHED

    @pytest.mark.asyncio
    async def test_an_unreadable_document_does_not_stop_the_bill(
        self, odoo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The document is evidence attached to the record. Holding up a payable
        because the PDF could not be read would be the wrong trade."""

        async def boom(_key: str) -> bytes:
            raise RuntimeError("R2 is down")

        monkeypatch.setattr(source_document.storage, "download_file", boom)

        _, outcome = await bcs.create_bill_for_invoice(
            _db(),
            invoice=_invoice(),
            po_id=PO_ID,
            ref="INV-4471",
            invoice_date=None,
            lines=[{"po_line_id": 10, "quantity": 50.0}],
            receive_goods=True,
            attach_document=True,
            reviewer_id=_uuid(),
        )

        assert outcome["status"] is BillOutcome.BILL_CREATED
        assert outcome["attachment_status"] is AttachmentStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_the_ocr_date_becomes_the_accounting_date(self, odoo) -> None:
        invoice = _invoice()
        invoice.extracted_date = dt.date(2026, 7, 1)

        _, outcome = await bcs.create_bill_for_invoice(
            _db(),
            invoice=invoice,
            po_id=PO_ID,
            ref="INV-4471",
            invoice_date=None,
            lines=[{"po_line_id": 10, "quantity": 50.0}],
            receive_goods=False,
            attach_document=False,
            reviewer_id=_uuid(),
        )

        assert outcome["invoice_date"] == dt.date(2026, 7, 1)
        assert odoo["created"]["invoice_date"] == "2026-07-01"

    @pytest.mark.asyncio
    async def test_the_audit_record_is_assigned_not_mutated(self, odoo) -> None:
        """`extra` is plain JSONB with no MutableDict, so an in-place write
        flushes nothing and the audit record silently never lands."""
        captured: dict[str, Any] = {}

        class _CapturingRepo(_FakeRepo):
            async def update(self, _invoice: Any, **fields: Any) -> None:
                captured.update(fields)

        bcs.MatchHistoryRepository = _CapturingRepo  # type: ignore[misc]
        try:
            invoice = _invoice()
            original = invoice.extra

            await bcs.create_bill_for_invoice(
                _db(),
                invoice=invoice,
                po_id=PO_ID,
                ref="INV-4471",
                invoice_date=None,
                lines=[{"po_line_id": 10, "quantity": 50.0}],
                receive_goods=False,
                attach_document=False,
                reviewer_id=_uuid(),
            )
        finally:
            bcs.MatchHistoryRepository = _FakeRepo  # type: ignore[misc]

        assert captured["extra"] is not original
        assert captured["extra"]["odoo_bill"]["id"] == 7788
        # The mapping actually used — the only record of which order line each
        # quantity landed on.
        assert captured["extra"]["odoo_bill"]["lines"] == [
            {"po_line_id": 10, "quantity": 50.0, "description": "Widget A"}
        ]
        assert captured["odoo_bill_id"] == 7788
        assert captured["pushed_to_odoo"] is True


# ---------------------------------------------------------------- test doubles
def _uuid():
    return uuid.uuid4()


def _invoice():
    class _Invoice:
        id = _uuid()
        company_id = _uuid()
        tenant_id = "default"
        file_name = "invoice.pdf"
        file_key = "default/2026/08/invoice.pdf"
        mime_type = "application/pdf"
        uploaded_by = None
        matched_po_id = PO_ID
        final_po_id = PO_ID
        pushed_to_odoo = False
        odoo_bill_id = None
        odoo_bill_ref = None
        extracted_invoice_no = "INV-4471"
        extracted_date = None
        extra: dict[str, Any] = {}
        extracted_json = {
            "vendor_name": "Acme Tools Ltd",
            "invoice_number": "INV-4471",
            "currency": "AED",
            "items": [],
            "untaxed_amount": 500.0,
            "tax_amount": 25.0,
            "total_amount": 525.0,
        }

    return _Invoice()


def _db():
    class _Session:
        async def commit(self) -> None:
            return None

    return _Session()


class _FakeRepo:
    def __init__(self, _db: Any) -> None:
        pass

    async def update(self, _invoice: Any, **_: Any) -> None:
        return None


class _FakeNotifier:
    def __init__(self, _db: Any) -> None:
        pass

    async def notify_user(self, **_: Any) -> None:
        return None


class _FakeApprovals:
    def __init__(self, _db: Any) -> None:
        pass

    async def gate_for_billing(self, _invoice: Any) -> None:
        """None means "this company has no active chain", so billing is
        unchanged — the behaviour every company has until an admin switches a
        chain on, and the one these tests describe."""
        return None
