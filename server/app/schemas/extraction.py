"""The structured shape extracted from an invoice PDF.

This module is used three ways from one definition:

  1. It is the JSON schema sent to Mistral as `document_annotation_format`, so
     the model is told exactly what to return.
  2. It validates whatever comes back. Anything that does not satisfy it is a
     failed extraction, never a half-populated database row.
  3. It is the input to the matching engine.

Keeping those three in one file is the point. A prompt that describes one shape
while the validator expects another is a bug that only appears in production,
on the one invoice whose layout differs.

Every `description` here becomes part of the JSON schema Mistral receives, so
the extraction rules travel with the schema rather than living in a separate
prompt string that can drift away from it.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Mistral's structured output runs in strict mode, where every field must be a
# plain JSON type. Dates therefore arrive as strings and are coerced here, in
# `InvoiceExtraction`, rather than being declared as `date` on the wire.
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# Tried in order when the model ignores the YYYY-MM-DD instruction. Ordered
# most-specific first; the ambiguous D/M vs M/D pair is resolved day-first
# because these invoices are predominantly non-US.
_FALLBACK_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)


def _coerce_float(value: Any) -> float:
    """Turn whatever the model produced into a number.

    Models return "1,234.56", "$1234.56" and "(500.00)" despite being told not
    to. Salvaging those is worth doing — the alternative is discarding an
    otherwise perfect extraction over a currency symbol.
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")  # accounting negative
    cleaned = re.sub(r"[^\d.\-]", "", text.strip("()"))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return 0.0
    try:
        number = float(cleaned)
    except ValueError:
        return 0.0
    return -number if negative else number


#: Injected for any key the model leaves out.
#:
#: Every field below is declared WITHOUT a Pydantic default, which is what puts
#: it in the JSON schema's `required` list. That turned out to matter a great
#: deal: with all fields optional, mistral-ocr returned only `items` and the
#: totals and silently omitted vendor_name, po_number, order_date and every
#: unit_price — all legal against the schema it was given. Marking them required
#: and nullable instead forces the model to look for each one and answer null
#: when it genuinely is not there.
#:
#: Our own parsing must still tolerate a missing key, hence this table and the
#: `mode="before"` validators that apply it.
_ITEM_DEFAULTS: dict[str, Any] = {
    "name": "",
    "product_code": None,
    "uom": None,
    "quantity": 0.0,
    "unit_price": 0.0,
    "subtotal": 0.0,
    "tax": 0.0,
}

_EXTRACTION_DEFAULTS: dict[str, Any] = {
    "vendor_name": None,
    "vendor_email": None,
    "vendor_address": None,
    "po_number": None,
    "order_date": None,
    "currency": "USD",
    "items": [],
    "untaxed_amount": 0.0,
    "tax_amount": 0.0,
    "total_amount": 0.0,
}


class ExtractedLineItem(BaseModel):
    """One row of the invoice's item table."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        description=(
            "The product description exactly as printed, in its original "
            "language and script. Do not translate it."
        )
    )
    product_code: str | None = Field(
        description=(
            "Any SKU, item code, article number or product ID printed against "
            "this line, including one embedded in the description such as "
            "'Product ID: 4426' or a bracketed reference like '[AVO-01]'. "
            "null if the line carries none."
        )
    )
    uom: str | None = Field(
        description=(
            "Unit of measure as printed — kg, pcs, box, ltr, carton. null if "
            "the document does not state one."
        )
    )
    quantity: float = Field(
        description="Number of units ordered, as a number. 0 if not printed."
    )
    unit_price: float = Field(
        description="Cost per single unit, as a number. 0 if not printed."
    )
    subtotal: float = Field(
        description="Line total: quantity multiplied by unit_price."
    )
    tax: float = Field(
        description=(
            "Tax charged on THIS line alone, as a number, when the document "
            "prints tax per line. 0 when the document states tax only once for "
            "the whole invoice — never divide the invoice's total tax across "
            "the lines yourself."
        )
    )

    @model_validator(mode="before")
    @classmethod
    def _fill_absent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {**_ITEM_DEFAULTS, **data}
        return data

    @field_validator("quantity", "unit_price", "subtotal", "tax", mode="before")
    @classmethod
    def _numeric(cls, v: Any) -> float:
        return _coerce_float(v)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> str:
        # A nameless line is still a line — the amounts are what matching uses.
        return (str(v).strip() if v is not None else "") or "(unnamed item)"

    @field_validator("product_code", "uom", mode="before")
    @classmethod
    def _optional_text(cls, v: Any) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        if not text or text.lower() in {"null", "none", "n/a", "na", "-"}:
            return None
        return text[:120]

    @model_validator(mode="after")
    def _fill_subtotal(self) -> "ExtractedLineItem":
        """Derive the subtotal when the document does not print one.

        Only when it is missing. A printed subtotal that disagrees with
        quantity x unit_price is left exactly as printed — that disagreement is
        usually a line discount, and silently "correcting" it would destroy the
        evidence a reviewer needs.
        """
        if self.subtotal == 0.0 and self.quantity and self.unit_price:
            object.__setattr__(self, "subtotal", round(self.quantity * self.unit_price, 4))
        return self


class InvoiceExtraction(BaseModel):
    """Everything read off one invoice document."""

    model_config = ConfigDict(populate_by_name=True)

    # No field below carries a default — see _EXTRACTION_DEFAULTS for why.

    # ------------------------------------------------------------ vendor
    vendor_name: str | None = Field(
        description="Full legal name of the vendor or supplier. null if absent."
    )
    vendor_email: str | None = Field(
        description="Email address of the vendor. null if absent."
    )
    vendor_address: str | None = Field(
        description="Complete physical address of the vendor. null if absent."
    )

    # ------------------------------------------------------------ header
    po_number: str | None = Field(
        description=(
            "Purchase Order number, invoice number, reference number, or "
            "document ID. null if absent."
        ),
    )
    order_date: str | None = Field(
        description=(
            "Date of the order or invoice, formatted strictly as YYYY-MM-DD. "
            "null if absent."
        ),
    )
    currency: str = Field(
        description='Three-letter ISO code such as "USD", "EUR", "PKR", "AED".',
    )

    # ------------------------------------------------------------ lines
    items: list[ExtractedLineItem] = Field(
        description="Every row of the item table. Empty array if there is none."
    )

    # ------------------------------------------------------------ totals
    untaxed_amount: float = Field(description="Subtotal before taxes, as a number.")
    tax_amount: float = Field(description="Total tax amount added, as a number.")
    total_amount: float = Field(description="Final grand total, as a number.")

    # ------------------------------------------------------------ validators
    @model_validator(mode="before")
    @classmethod
    def _fill_absent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {**_EXTRACTION_DEFAULTS, **data}
        return data

    @field_validator("untaxed_amount", "tax_amount", "total_amount", mode="before")
    @classmethod
    def _numeric(cls, v: Any) -> float:
        return _coerce_float(v)

    @field_validator(
        "vendor_name", "vendor_email", "vendor_address", "po_number", mode="before"
    )
    @classmethod
    def _clean_optional(cls, v: Any) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        # Models emit these literal strings when a field is absent. Storing them
        # would make "null" a vendor name that fuzzy-matches other nulls.
        if not text or text.lower() in {"null", "none", "n/a", "na", "-", "unknown"}:
            return None
        return text[:500]

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v: Any) -> str:
        if not v:
            return "USD"
        text = str(v).strip().upper()
        # Symbols come back more often than codes despite the instruction.
        symbols = {"$": "USD", "€": "EUR", "£": "GBP", "₨": "PKR", "﷼": "SAR", "¥": "JPY"}
        if text in symbols:
            return symbols[text]
        return text[:10] if text.isalpha() and len(text) == 3 else (text[:10] or "USD")

    @field_validator("order_date", mode="before")
    @classmethod
    def _date(cls, v: Any) -> str | None:
        """Normalise to YYYY-MM-DD, or drop it.

        A wrong date is worse than no date: the matching engine gives date
        proximity real weight, so a misparsed year would actively push the
        correct purchase order out of the candidate list.
        """
        if v is None or v == "":
            return None

        text = str(v).strip()
        if _ISO_DATE.match(text):
            candidate = text[:10]
            try:
                dt.date.fromisoformat(candidate)
                return candidate
            except ValueError:
                pass

        for fmt in _FALLBACK_DATE_FORMATS:
            try:
                return dt.datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------ helpers
    @property
    def order_date_value(self) -> dt.date | None:
        """The parsed date, for code that wants a `date` rather than a string."""
        if not self.order_date:
            return None
        try:
            return dt.date.fromisoformat(self.order_date)
        except ValueError:
            return None

    @model_validator(mode="after")
    def _reconcile_totals(self) -> "InvoiceExtraction":
        """Fill in whichever total the document did not print.

        Purely arithmetic, and only ever applied to a field that is zero. The
        untaxed amount matters most: it is what a purchase order's
        `amount_untaxed` is compared against, and a missing one would silently
        cost the amount component of every match score.
        """
        if self.untaxed_amount == 0.0:
            if self.total_amount and self.tax_amount:
                object.__setattr__(
                    self, "untaxed_amount", round(self.total_amount - self.tax_amount, 4)
                )
            elif self.items:
                object.__setattr__(
                    self,
                    "untaxed_amount",
                    round(sum(item.subtotal for item in self.items), 4),
                )

        if self.total_amount == 0.0 and self.untaxed_amount:
            object.__setattr__(
                self, "total_amount", round(self.untaxed_amount + self.tax_amount, 4)
            )

        return self

    @property
    def is_usable(self) -> bool:
        """Whether there is enough here to attempt a match.

        A vendor name or a reference is the minimum: without one of them every
        candidate scores the same and the LLM is being asked to guess.
        """
        return bool(self.vendor_name or self.po_number) and (
            self.total_amount > 0 or self.untaxed_amount > 0 or bool(self.items)
        )


class DocumentExtraction(BaseModel):
    """Every invoice found in one uploaded file.

    A PDF is not reliably one invoice. Scanning a stack of paper, exporting a
    day's purchases from an ERP, or forwarding a bundled statement all produce
    a single file containing several distinct documents — different vendors,
    different references, different totals.

    Extracting into a single `InvoiceExtraction` silently kept the first and
    discarded the rest, which reads to a user as "OCR missed my data" when the
    OCR was in fact perfect. Modelling the document as a LIST is what makes
    that case representable at all; the service then splits it into one
    database row per invoice, because one row per invoice is what the rest of
    the system — matching, review, the Odoo bill — is built on.
    """

    model_config = ConfigDict(populate_by_name=True)

    invoices: list[InvoiceExtraction] = Field(
        description=(
            "One entry for EVERY separate invoice, bill or purchase order in "
            "this document. Most files contain exactly one; a scanned batch or "
            "an ERP export may contain several. A new invoice starts wherever a "
            "different document number, vendor or totals block appears — a "
            "single invoice continuing across a page break is NOT a new entry. "
            "Never merge two invoices into one, and never split one invoice in "
            "two."
        )
    )

    @model_validator(mode="before")
    @classmethod
    def _fill_absent(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Tolerate a model that answered with a bare invoice object rather
            # than the wrapper — the shape is unambiguous either way.
            if "invoices" not in data and "vendor_name" in data:
                return {"invoices": [data]}
            return {"invoices": data.get("invoices") or []}
        if isinstance(data, list):
            return {"invoices": data}
        return data

    @property
    def primary(self) -> InvoiceExtraction:
        """The first invoice, or an empty one when nothing was readable."""
        return self.invoices[0] if self.invoices else InvoiceExtraction.model_validate({})
