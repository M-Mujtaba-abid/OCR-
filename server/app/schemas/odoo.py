"""Normalised Odoo records.

Odoo's XML-RPC returns many-to-one fields as `[id, "display name"]` pairs and
absent values as `False` rather than null. Nothing outside `odoo_service` should
have to know that, so every record is normalised into these models at the
boundary.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: How many order lines are carried into a projection — the LLM prompt and the
#: audit blob alike. Capped because a 200-line order would otherwise dominate
#: the prompt and crowd out the other candidates entirely.
LINE_PROJECTION_CAP = 25


def _odoo_value(value: Any) -> Any:
    """Odoo writes an absent value as `False`, not null."""
    return None if value is False else value


def _relation_id(value: Any) -> int | None:
    """`[7, "Acme Tools Ltd"]` -> `7`."""
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    return None


def _relation_name(value: Any) -> str | None:
    """`[7, "Acme Tools Ltd"]` -> `"Acme Tools Ltd"`."""
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return str(value[1])
    return None


class OdooPurchaseOrderLine(BaseModel):
    """One `purchase.order.line`."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    order_id: int | None = None
    name: str = ""
    product_id: int | None = None
    product_name: str | None = None
    product_qty: float = 0.0
    qty_invoiced: float = 0.0
    price_unit: float = 0.0
    price_subtotal: float = 0.0
    #: Tax charged on this line, and the line total including it. Odoo computes
    #: both, so they are read rather than derived — a line can carry several
    #: taxes, and re-deriving them from a rate here would eventually disagree
    #: with the bill Odoo itself produces.
    price_tax: float = 0.0
    price_total: float = 0.0
    uom: str | None = None

    @model_validator(mode="after")
    def _backfill_total(self) -> "OdooPurchaseOrderLine":
        """Fall back to subtotal + tax when the total is absent.

        Records that predate these fields — the JSON fixture, anything already
        serialised — validate straight into this model without going through
        `from_odoo`. Without this they would display a line total of 0.00,
        which reads as "this line is free" rather than "this is old data".
        """
        if not self.price_total:
            self.price_total = self.price_subtotal + self.price_tax
        return self

    @classmethod
    def from_odoo(cls, row: dict[str, Any]) -> "OdooPurchaseOrderLine":
        return cls(
            id=int(row["id"]),
            order_id=_relation_id(row.get("order_id")),
            name=str(_odoo_value(row.get("name")) or ""),
            product_id=_relation_id(row.get("product_id")),
            product_name=_relation_name(row.get("product_id")),
            product_qty=float(_odoo_value(row.get("product_qty")) or 0.0),
            qty_invoiced=float(_odoo_value(row.get("qty_invoiced")) or 0.0),
            price_unit=float(_odoo_value(row.get("price_unit")) or 0.0),
            price_subtotal=float(_odoo_value(row.get("price_subtotal")) or 0.0),
            price_tax=float(_odoo_value(row.get("price_tax")) or 0.0),
            price_total=float(_odoo_value(row.get("price_total")) or 0.0),
            uom=_relation_name(row.get("product_uom")),
        )


class OdooPurchaseOrder(BaseModel):
    """One `purchase.order`, with its lines attached."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    partner_id: int | None = None
    partner_name: str | None = None
    partner_ref: str | None = Field(
        default=None, description="The vendor's own reference for this order."
    )
    date_order: dt.date | None = None
    amount_untaxed: float = 0.0
    amount_tax: float = 0.0
    amount_total: float = 0.0
    currency: str | None = None
    state: str | None = None
    invoice_status: str | None = None
    lines: list[OdooPurchaseOrderLine] = Field(default_factory=list)

    @field_validator("date_order", mode="before")
    @classmethod
    def _parse_date(cls, v: Any) -> dt.date | None:
        # `date_order` is a datetime in Odoo ("2026-08-10 09:15:00") but only
        # the date is ever compared, and keeping the time would make an
        # exact-day comparison fail for no reason.
        value = _odoo_value(v)
        if value is None:
            return None
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    @classmethod
    def from_odoo(cls, row: dict[str, Any]) -> "OdooPurchaseOrder":
        return cls(
            id=int(row["id"]),
            name=str(_odoo_value(row.get("name")) or f"PO-{row['id']}"),
            partner_id=_relation_id(row.get("partner_id")),
            partner_name=_relation_name(row.get("partner_id")),
            partner_ref=_odoo_value(row.get("partner_ref")) or None,
            date_order=row.get("date_order"),
            amount_untaxed=float(_odoo_value(row.get("amount_untaxed")) or 0.0),
            amount_tax=float(_odoo_value(row.get("amount_tax")) or 0.0),
            amount_total=float(_odoo_value(row.get("amount_total")) or 0.0),
            currency=_relation_name(row.get("currency_id")),
            state=_odoo_value(row.get("state")) or None,
            invoice_status=_odoo_value(row.get("invoice_status")) or None,
        )

    def line_items(self, limit: int = LINE_PROJECTION_CAP) -> list[dict[str, Any]]:
        """The order's lines, as shown to a reviewer.

        Persisted into the candidate audit blob so the review screen can say
        what an order actually contains. A line-items score of 0 is only
        actionable if the reviewer can see that this order is for mangoes and
        the invoice is for apples — otherwise the only way to find out is to
        open Odoo, which is the thing this product exists to avoid.
        """
        return [
            {
                "name": line.product_name or line.name,
                "quantity": round(line.product_qty, 3),
                "unit_price": round(line.price_unit, 2),
                "subtotal": round(line.price_subtotal, 2),
                "price_tax": round(line.price_tax, 2),
                "price_total": round(line.price_total, 2),
            }
            for line in self.lines[:limit]
        ]

    def for_prompt(self, *, item_limit: int = LINE_PROJECTION_CAP) -> dict[str, Any]:
        """A compact projection for the LLM.

        Trimmed on purpose: every token in the prompt is billed, and fields the
        model cannot use to decide — internal ids beyond the one it must return,
        Odoo state machinery — only give it more to be confused by.

        Absent fields are omitted rather than sent as null. `"vendor_ref": null`
        is billed on every candidate to say nothing; a missing key says the same
        thing for free, and the model reads it the same way.
        """
        projection: dict[str, Any] = {
            "po_id": self.id,
            "po_number": self.name,
            "vendor": self.partner_name,
            "vendor_ref": self.partner_ref,
            "order_date": self.date_order.isoformat() if self.date_order else None,
            "amount_untaxed": round(self.amount_untaxed, 2),
            "amount_total": round(self.amount_total, 2),
            "currency": self.currency,
            # Whether Odoo still expects a bill for this order. Kept despite
            # the trimming above: an order that is already invoiced can still
            # be the right one, and the model has to be able to say so.
            "invoice_status": self.invoice_status,
            "items": [
                {
                    "name": line.product_name or line.name,
                    "quantity": round(line.product_qty, 3),
                    "unit_price": round(line.price_unit, 2),
                    "subtotal": round(line.price_subtotal, 2),
                }
                # Deliberately leaner than `line_items` — the model does not
                # need per-line tax to pick an order, and every field here is
                # billed once per candidate.
                for line in self.lines[:item_limit]
            ],
        }
        return {k: v for k, v in projection.items() if v is not None and v != []}
