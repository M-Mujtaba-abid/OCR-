"""Resolving extracted text to Odoo records, and refusing to.

Every case here is one that was measured against the real Odoo before this
feature was designed — the numbers in the stubs are the numbers that came back.
That is the point: if someone later loosens a threshold, the test that breaks
is the one describing the invoice that motivated it.

No network. The two Odoo searches are stubbed, so what is under test is the
judgement, not the transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.exceptions import InvoiceNotReadyError
from app.schemas.odoo import OdooAttachment
from app.services import po_creator_service as poc
from app.services.odoo_service import match_recent_draft

#: Names as they actually appear in this Odoo, including the Arabic halves —
#: which is half the reason the naive search failed.
PARTNERS = [
    "A J K Restaurants Management Llc",
    "Independent Restaurants Management L.L.Cانديبندنت لإدارة المطاعم ش.ذ.م.م",
    "Gvn Restaurants Management Llc",
    "Berry Mount Vegetables And Fruit Trading Llc",
]

PRODUCTS = [
    "Eggplant باذنجان",
    "Egg Plant Seedless الباذنجان بدون بذور",
    "Baby EggPlant طفل الباذنجان",
    "EGG بيضة",
    "Lemon ليمون",
    "Sanitized lemon الليمون المعقم",
    "Lemon Leaves أوراق الليمون",
    "ASSORTED FLOWER  زهرة متنوعة",
]


def _rows(pool: list[str], names: list[str]) -> list[dict[str, Any]]:
    """Ids are the record's position in its pool, so they stay stable.

    A search that renumbered its own results would let `read_names` disagree
    with what the search returned — the test double would then be lying about
    the one thing creation re-checks.
    """
    return [{"id": pool.index(name) + 1, "display_name": name} for name in names]


class _FakeOdoo:
    """Stands in for ONE company's OdooService.

    An object rather than a set of patched module functions, because that is
    what the code now takes: every po_creator entry point is handed the Odoo it
    should talk to, so a test that wants to control Odoo hands it one.

    Subscriptable so the creation tests can still read the payload that was
    sent — `odoo["order_lines"]` is what `create_draft_purchase_order` received.
    """

    def __init__(self) -> None:
        self.created: dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        return self.created[key]


@pytest.fixture
def odoo(monkeypatch: pytest.MonkeyPatch):
    """Stub the two searches; record what was created."""
    fake = _FakeOdoo()
    created = fake.created

    async def fake_search(model: str, tokens: list[str], **_: Any) -> list[dict[str, Any]]:
        if not tokens:
            return []
        pool = PARTNERS if model == "res.partner" else PRODUCTS
        # The real search is an OR-ilike over the tokens; this mirrors it.
        return _rows(pool, [n for n in pool if any(t in n.casefold() for t in tokens)])

    async def fake_read_names(model: str, ids: list[int]) -> dict[int, str]:
        pool = PARTNERS if model == "res.partner" else PRODUCTS
        return {i: pool[i - 1] for i in ids if 0 < i <= len(pool)}

    async def fake_create(**kwargs: Any):
        created.update(kwargs)
        from app.schemas.odoo import OdooCreatedOrder

        # Mirrors the real one: it reports what became of the document rather
        # than raising, because the order exists either way.
        return OdooCreatedOrder(
            id=1690,
            name="P01690",
            attachment_status="attached" if kwargs.get("attachment") else "none",
            attachment_id=99 if kwargs.get("attachment") else None,
        )

    async def fake_document(_invoice: Any):
        return OdooAttachment(
            file_name="note.jpg",
            mime_type="image/jpeg",
            content=bytes.fromhex("ffd8ff") + b" scan",
        )

    fake.search_by_tokens = fake_search  # type: ignore[attr-defined]
    fake.read_names = fake_read_names  # type: ignore[attr-defined]
    fake.create_draft_purchase_order = fake_create  # type: ignore[attr-defined]

    async def fake_resolve(_db: Any, _invoice: Any) -> _FakeOdoo:
        return fake

    # `create_po_for_invoice` resolves its own company's Odoo from the invoice.
    # This is the seam that replaces patching a module-level singleton, and it
    # is a truer stub: the test now supplies the company's connection rather
    # than overwriting a global everybody shared.
    monkeypatch.setattr(poc, "odoo_for_invoice", fake_resolve)
    # Stubbed, or every test in this file reaches for object storage.
    monkeypatch.setattr(poc, "read_source_document", fake_document)
    return fake


class TestVendorResolution:
    @pytest.mark.asyncio
    async def test_a_spaced_initialism_resolves(self, odoo) -> None:
        """Odoo writes "A J K", the vendor's paper writes "AJK"."""
        match = await poc.resolve_vendor(odoo, "AJK Restaurants")

        assert match is not None
        assert match.name == "A J K Restaurants Management Llc"

    @pytest.mark.asyncio
    async def test_an_ocr_mangling_resolves_to_nothing(self, odoo) -> None:
        """"Retardant" for "Restaurants" — measured at 32, must not be guessed."""
        assert await poc.resolve_vendor(odoo, "AJK Retardant") is None

    @pytest.mark.asyncio
    async def test_a_close_runner_up_blocks_resolution(self, odoo) -> None:
        """The Odoo name itself scores 100, but Gvn/Independent score 91.7.

        Raising an order against the wrong company is not a lesser error than
        raising none, so this refuses rather than picking the top of a photo
        finish.
        """
        assert await poc.resolve_vendor(odoo, "Restaurants Management Llc") is None

    @pytest.mark.asyncio
    async def test_an_unmistakable_vendor_resolves(self, odoo) -> None:
        match = await poc.resolve_vendor(odoo, "Berry Mount Vegetables And Fruit Trading")

        assert match is not None
        assert match.id == 4


class TestProductCandidates:
    @pytest.mark.asyncio
    async def test_a_confidently_wrong_match_is_never_preselected(self, odoo) -> None:
        """The case this whole flow exists for.

        "Egg Plant (C. Int.)" scores highest against "Egg Plant Seedless" — the
        wrong product — while the right one ranks third. No threshold catches
        that, so the reviewer is asked.
        """
        candidates = await poc.product_candidates(odoo, "Egg Plant (C. Int.)")

        assert candidates  # options are offered
        assert poc._preselect(candidates) is None

    @pytest.mark.asyncio
    async def test_a_three_way_tie_is_never_preselected(self, odoo) -> None:
        """Lemon, Sanitized lemon and Lemon Leaves score alike."""
        candidates = await poc.product_candidates(odoo, "J5 (lemon)")

        assert len(candidates) >= 3
        assert poc._preselect(candidates) is None

    @pytest.mark.asyncio
    async def test_an_unmistakable_product_is_preselected(self, odoo) -> None:
        """100 against a distant runner-up — confirming, not choosing."""
        candidates = await poc.product_candidates(odoo, "Assorted Flower")

        assert poc._preselect(candidates) == candidates[0].id
        assert candidates[0].name.startswith("ASSORTED FLOWER")

    @pytest.mark.asyncio
    async def test_candidates_are_capped(self, odoo) -> None:
        candidates = await poc.product_candidates(odoo, "lemon egg plant flower")

        assert len(candidates) <= poc.PRODUCT_CANDIDATES


class TestDuplicateGuard:
    """A create that failed on the way back must not become two orders.

    This is the rule, not the Odoo search that feeds it: three identical
    30,000 AED drafts existed before the crash behind them was visible, and
    each one came from a reviewer reasonably clicking again.
    """

    def test_an_identical_recent_draft_is_recognised(self) -> None:
        rows = [
            {"id": 1692, "name": "P01692", "amount_untaxed": 30000.0},
            {"id": 1500, "name": "P01500", "amount_untaxed": 25.0},
        ]

        match = match_recent_draft(rows, 30000.0)

        assert match is not None
        assert match["name"] == "P01692"

    def test_rounding_does_not_defeat_it(self) -> None:
        """15 x 2000.001 is the same order as 30000.02, not a new one."""
        assert match_recent_draft([{"id": 1, "name": "P1", "amount_untaxed": 30000.02}], 30000.015)

    def test_a_different_amount_is_a_different_order(self) -> None:
        rows = [{"id": 1692, "name": "P01692", "amount_untaxed": 30000.0}]

        assert match_recent_draft(rows, 29000.0) is None

    def test_nothing_recent_means_nothing_to_match(self) -> None:
        assert match_recent_draft([], 30000.0) is None


class TestCreation:
    @pytest.mark.asyncio
    async def test_a_line_without_a_product_blocks_the_whole_order(
        self, odoo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And the error names the line, so the screen can point at it."""
        invoice = _invoice()

        with pytest.raises(InvoiceNotReadyError) as exc:
            await poc.create_po_for_invoice(
                _db(),
                invoice=invoice,
                partner_id=1,
                order_date="2026-08-17",
                lines=[
                    {"line_no": 1, "product_id": 1, "description": "A", "quantity": 1, "unit_price": 1},
                    {"line_no": 2, "product_id": None, "description": "B", "quantity": 1, "unit_price": 1},
                ],
                reviewer_id=_uuid(),
            )

        assert "Line 2" in exc.value.message

    @pytest.mark.asyncio
    async def test_the_payload_carries_no_tax(self, odoo, monkeypatch) -> None:
        """Odoo owns tax. An OCR'd figure must not overwrite its configuration."""
        monkeypatch.setattr(poc, "MatchHistoryRepository", _FakeRepo)
        monkeypatch.setattr(poc, "NotificationService", _FakeNotifier)

        await poc.create_po_for_invoice(
            _db(),
            invoice=_invoice(),
            partner_id=1,
            order_date="2026-08-17",
            lines=[
                {"line_no": 1, "product_id": 5, "description": "J5 (lemon)",
                 "quantity": 1, "unit_price": 7.02}
            ],
            reviewer_id=_uuid(),
        )

        [line] = odoo["order_lines"]
        assert set(line) == {"product_id", "name", "product_qty", "price_unit"}
        assert odoo["date_order"] == "2026-08-17 00:00:00"

    @pytest.mark.asyncio
    async def test_the_scan_goes_onto_the_order(self, odoo, monkeypatch) -> None:
        """The paper the order was raised from, filed with it.

        Without this the person confirming the RFQ in Odoo has only figures to
        check against — and a bill raised from the order inside Odoo inherits no
        document either, which is how the same PDF gets uploaded by hand.
        """
        monkeypatch.setattr(poc, "MatchHistoryRepository", _FakeRepo)
        monkeypatch.setattr(poc, "NotificationService", _FakeNotifier)

        await poc.create_po_for_invoice(
            _db(),
            invoice=_invoice(),
            partner_id=1,
            order_date="2026-08-17",
            lines=[
                {"line_no": 1, "product_id": 5, "description": "J5 (lemon)",
                 "quantity": 1, "unit_price": 7.02}
            ],
            reviewer_id=_uuid(),
        )

        assert odoo["attachment"] is not None
        assert odoo["attachment"].file_name == "note.jpg"
        # And the outcome is recorded, so a missing scan is answerable later
        # rather than being something nobody can reconstruct.
        assert _FakeRepo.last["extra"]["odoo_po"]["attachment"] == "attached"

    @pytest.mark.asyncio
    async def test_an_unreadable_scan_does_not_stop_the_order(
        self, odoo, monkeypatch
    ) -> None:
        """Storage being down must not block a purchase order."""
        monkeypatch.setattr(poc, "MatchHistoryRepository", _FakeRepo)
        monkeypatch.setattr(poc, "NotificationService", _FakeNotifier)

        async def no_document(_invoice):
            return None

        monkeypatch.setattr(poc, "read_source_document", no_document)

        invoice = await poc.create_po_for_invoice(
            _db(),
            invoice=_invoice(),
            partner_id=1,
            order_date="2026-08-17",
            lines=[
                {"line_no": 1, "product_id": 5, "description": "J5 (lemon)",
                 "quantity": 1, "unit_price": 7.02}
            ],
            reviewer_id=_uuid(),
        )

        assert invoice is not None
        assert odoo["attachment"] is None
        assert _FakeRepo.last["extra"]["odoo_po"]["attachment"] == "none"

    @pytest.mark.asyncio
    async def test_a_product_archived_since_the_preview_is_caught_first(
        self, odoo
    ) -> None:
        """Before the write, not as a raw Odoo fault half way through it."""
        with pytest.raises(InvoiceNotReadyError) as exc:
            await poc.create_po_for_invoice(
                _db(),
                invoice=_invoice(),
                partner_id=1,
                lines=[{"line_no": 1, "product_id": 999, "description": "x",
                        "quantity": 1, "unit_price": 1}],
                order_date=None,
                reviewer_id=_uuid(),
            )

        assert exc.value.code == "PRODUCT_NOT_FOUND"


# ---------------------------------------------------------------- test doubles
def _uuid():
    import uuid

    return uuid.uuid4()


def _invoice():
    class _Invoice:
        id = _uuid()
        tenant_id = "default"
        file_name = "note.jpg"
        mime_type = "image/jpeg"
        file_key = "invoices/default/note.jpg"
        uploaded_by = None
        #: The order's audit blob is written into this, as the bill's is.
        extra: dict[str, object] = {}
        extracted_json = {
            "vendor_name": "AJK Restaurants",
            "order_date": "2026-08-17",
            "currency": "AED",
            "items": [],
            "untaxed_amount": 1.0,
            "tax_amount": 0.05,
            "total_amount": 1.05,
        }

    return _Invoice()


def _db():
    class _Session:
        async def commit(self) -> None:
            return None

    return _Session()


class _FakeRepo:
    #: What the last update wrote, so a test can read the audit blob back.
    last: dict[str, Any] = {}

    def __init__(self, _db: Any) -> None:
        pass

    async def update(self, _invoice: Any, **fields: Any) -> None:
        _FakeRepo.last = fields
        return None


class _FakeNotifier:
    def __init__(self, _db: Any) -> None:
        pass

    async def notify_user(self, **_: Any) -> None:
        return None
