"""Which purchase orders an invoice is scored against.

The regression these guard is a real one: an invoice whose purchase order Odoo
had already marked "invoiced" matched nothing at all, because the fetch filtered
that order out before the scoring pass ever saw it. The screen then said "no
purchase order scored highly enough" — indistinguishable, to a reviewer, from
the order not existing.

No database and no network: the two fetches are stubbed, so what is under test
is the pool-building decision itself.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.config import settings
from app.schemas.extraction import InvoiceExtraction
from app.schemas.odoo import OdooPurchaseOrder
from app.services import match_service


def make_invoice(order_date: str | None = "2026-07-22") -> InvoiceExtraction:
    return InvoiceExtraction.model_validate(
        {
            "vendor_name": "AJK Restaurants",
            "order_date": order_date,
            "currency": "AED",
            "items": [
                {"name": "J5 (lemon)", "quantity": 1, "unit_price": 7.02, "subtotal": 7.02}
            ],
            "untaxed_amount": 7.02,
            "total_amount": 7.02,
        }
    )


def make_order(po_id: int, name: str, invoice_status: str) -> OdooPurchaseOrder:
    return OdooPurchaseOrder.model_validate(
        {
            "id": po_id,
            "name": name,
            "partner_name": "A J K Restaurants Management Llc",
            "date_order": dt.date(2026, 7, 22),
            "amount_untaxed": 7.02,
            "amount_total": 7.02,
            "currency": "AED",
            "state": "purchase",
            "invoice_status": invoice_status,
        }
    )


@pytest.fixture
def fetches(monkeypatch: pytest.MonkeyPatch):
    """Stub both Odoo fetches and record what they were asked for."""
    calls: dict[str, object] = {"billed_since": None, "billed_called": False}
    open_orders = [make_order(1650, "P01650", "to invoice")]
    billed_orders = [make_order(1642, "P01642", "invoiced")]

    async def fake_open(**_: object) -> list[OdooPurchaseOrder]:
        return list(open_orders)

    async def fake_billed(*, since: dt.date, **_: object) -> list[OdooPurchaseOrder]:
        calls["billed_since"] = since
        calls["billed_called"] = True
        return list(billed_orders)

    monkeypatch.setattr(
        match_service.odoo_service, "fetch_open_purchase_orders", fake_open
    )
    monkeypatch.setattr(
        match_service.odoo_service, "fetch_recently_billed_orders", fake_billed
    )
    return calls, open_orders, billed_orders


class TestOrdersToConsider:
    @pytest.mark.asyncio
    async def test_already_billed_orders_are_scored_too(self, fetches) -> None:
        """The whole point: a billed order must reach the scoring pass."""
        _, _, _ = fetches
        orders = await match_service._orders_to_consider(make_invoice())

        assert [o.name for o in orders] == ["P01650", "P01642"]

    @pytest.mark.asyncio
    async def test_an_order_in_both_fetches_appears_once(self, fetches) -> None:
        """Overlapping windows must not double a candidate in the shortlist."""
        _, open_orders, _ = fetches
        open_orders.append(make_order(1642, "P01642", "invoiced"))

        orders = await match_service._orders_to_consider(make_invoice())

        assert [o.id for o in orders] == [1650, 1642]

    @pytest.mark.asyncio
    async def test_the_window_is_anchored_to_the_invoice_date(self, fetches) -> None:
        """An invoice dated months ago must look back from its own date."""
        calls, _, _ = fetches
        lookback = settings.MATCH_CLOSED_LOOKBACK_DAYS

        await match_service._orders_to_consider(make_invoice("2026-07-22"))

        assert calls["billed_since"] == dt.date(2026, 7, 22) - dt.timedelta(
            days=lookback
        )

    @pytest.mark.asyncio
    async def test_an_undated_invoice_still_sweeps(self, fetches) -> None:
        """No date on the document is not a reason to see fewer orders."""
        calls, _, _ = fetches

        orders = await match_service._orders_to_consider(make_invoice(None))

        assert calls["billed_called"] is True
        assert [o.name for o in orders] == ["P01650", "P01642"]

    @pytest.mark.asyncio
    async def test_lookback_of_zero_switches_the_sweep_off(
        self, fetches, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment that wants strictly billable orders can have that."""
        calls, _, _ = fetches
        monkeypatch.setattr(settings, "MATCH_CLOSED_LOOKBACK_DAYS", 0)

        orders = await match_service._orders_to_consider(make_invoice())

        assert calls["billed_called"] is False
        assert [o.name for o in orders] == ["P01650"]
