"""Normalised Odoo records.

Odoo's XML-RPC returns many-to-one fields as `[id, "display name"]` pairs and
absent values as `False` rather than null. Nothing outside `odoo_service` should
have to know that, so every record is normalised into these models at the
boundary.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    uom: str | None = None

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

    def for_prompt(self) -> dict[str, Any]:
        """A compact projection for the LLM.

        Trimmed on purpose: every token in the prompt is billed, and fields the
        model cannot use to decide — internal ids beyond the one it must return,
        Odoo state machinery — only give it more to be confused by.
        """
        return {
            "po_id": self.id,
            "po_number": self.name,
            "vendor": self.partner_name,
            "vendor_ref": self.partner_ref,
            "order_date": self.date_order.isoformat() if self.date_order else None,
            "amount_untaxed": round(self.amount_untaxed, 2),
            "amount_total": round(self.amount_total, 2),
            "currency": self.currency,
            "items": [
                {
                    "name": line.product_name or line.name,
                    "quantity": round(line.product_qty, 3),
                    "unit_price": round(line.price_unit, 2),
                    "subtotal": round(line.price_subtotal, 2),
                }
                # Capped: a 200-line order would otherwise dominate the prompt
                # and crowd out the other candidates entirely.
                for line in self.lines[:25]
            ],
        }
