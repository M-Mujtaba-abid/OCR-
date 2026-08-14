"""Score an extracted invoice against candidate purchase orders.

Pure and I/O-free by design: no database, no network, no clock. That makes it
directly unit-testable, and it makes the weights safe to tune — a change here
can be evaluated against fixtures in a second rather than against a live Odoo.

This stage is what keeps the design affordable. It reduces however many orders
are open to a handful the LLM can reason about properly, so the model does the
judgement it is good at instead of the search it is bad at.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.schemas.extraction import InvoiceExtraction
from app.schemas.odoo import OdooPurchaseOrder

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
# These sum to 100 only when every component applies. A component that cannot
# be evaluated — no invoice date, no reference printed — is dropped and the
# rest are renormalised, so a sparse invoice is not silently capped at a low
# score just for being sparse.
WEIGHTS: dict[str, float] = {
    "vendor": 30.0,
    "amount": 25.0,
    "reference": 20.0,
    "date": 15.0,
    "lines": 10.0,
}

#: Legal-form noise. "Acme Tools Ltd" and "ACME TOOLS LIMITED" are the same
#: vendor, and leaving these in makes near-identical names score apart.
_LEGAL_SUFFIXES = {
    "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation",
    "co", "company", "plc", "gmbh", "sa", "sas", "bv", "nv", "pvt", "private",
    "pte", "llp", "lp", "ag", "srl", "spa", "oy", "ab", "as", "trading",
    "trade", "general", "est", "establishment", "fzco", "fze", "fzc", "dmcc",
}

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")

#: A reference is compared on its digits and letters alone: "PO-2026-0089",
#: "PO 2026/0089" and "po20260089" are the same reference written three ways.
_REF_NOISE = re.compile(r"[^a-z0-9]")


def normalise_vendor(name: str | None) -> str:
    """Lowercase, strip punctuation, drop legal-form words."""
    if not name:
        return ""
    text = _NON_ALNUM.sub(" ", name.lower())
    words = [w for w in _WHITESPACE.sub(" ", text).split() if w not in _LEGAL_SUFFIXES]
    return " ".join(words)


def normalise_reference(ref: str | None) -> str:
    return _REF_NOISE.sub("", ref.lower()) if ref else ""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class ScoredCandidate:
    """A purchase order with its score and how that score was reached."""

    order: OdooPurchaseOrder
    score: float
    #: Per-component scores, 0–100, before weighting. Only components that
    #: applied appear here.
    breakdown: dict[str, float] = field(default_factory=dict)
    #: Human-readable notes explaining each component.
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        """The shape persisted into `match_history.candidates`.

        Stored in full — including the losers — because that is what makes a
        wrong match arguable after the fact, and it is the data any future
        tuning of the weights would be evaluated against.
        """
        return {
            "po_id": self.order.id,
            "po_number": self.order.name,
            "vendor": self.order.partner_name,
            "amount_untaxed": round(self.order.amount_untaxed, 2),
            "amount_total": round(self.order.amount_total, 2),
            "order_date": (
                self.order.date_order.isoformat() if self.order.date_order else None
            ),
            "score": round(self.score, 1),
            "breakdown": {k: round(v, 1) for k, v in self.breakdown.items()},
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def _score_vendor(
    invoice: InvoiceExtraction, order: OdooPurchaseOrder
) -> tuple[float, str] | None:
    left = normalise_vendor(invoice.vendor_name)
    right = normalise_vendor(order.partner_name)
    if not left or not right:
        return None

    # token_set_ratio, not plain ratio: it ignores word order and extra words,
    # so "Acme Tools" matches "Tools, Acme International" — which is what
    # vendor names actually look like once two systems have both mangled them.
    score = float(fuzz.token_set_ratio(left, right))
    return score, f"vendor {invoice.vendor_name!r} vs {order.partner_name!r} = {score:.0f}"


def _score_amount(
    invoice: InvoiceExtraction, order: OdooPurchaseOrder
) -> tuple[float, str] | None:
    """Compare untaxed totals, in graded bands.

    Untaxed rather than gross: tax treatment routinely differs between a
    purchase order and the vendor's invoice — different rates, reverse charge,
    withholding — while the value of the goods does not.
    """
    invoice_amount = invoice.untaxed_amount or invoice.total_amount
    order_amount = order.amount_untaxed or order.amount_total
    if not invoice_amount or not order_amount:
        return None

    delta = abs(order_amount - invoice_amount) / max(order_amount, 1e-9)

    # Bands, not a linear falloff. A 0.4% difference is rounding; a 9%
    # difference is a different order, and the gap between them should not be
    # a gentle slope.
    if delta <= 0.005:
        score = 100.0
    elif delta <= 0.02:
        score = 90.0
    elif delta <= 0.05:
        score = 70.0
    elif delta <= 0.10:
        score = 45.0
    elif delta <= 0.25:
        score = 20.0
    else:
        score = 0.0

    return score, f"amount {invoice_amount:,.2f} vs {order_amount:,.2f} ({delta:.1%} apart)"


def _score_reference(
    invoice: InvoiceExtraction, order: OdooPurchaseOrder
) -> tuple[float, str] | None:
    """Match the invoice's printed reference against the order's identifiers.

    The strongest signal there is when it is present — a vendor quoting the PO
    number is stating the answer — which is why an exact hit scores 100 and a
    containment hit still scores highly.
    """
    invoice_ref = normalise_reference(invoice.po_number)
    if not invoice_ref:
        return None

    targets = [
        (normalise_reference(order.name), "PO number"),
        (normalise_reference(order.partner_ref), "vendor ref"),
    ]

    best = 0.0
    label = ""
    for target, which in targets:
        if not target:
            continue
        if invoice_ref == target:
            score = 100.0
        elif len(invoice_ref) >= 4 and (
            invoice_ref in target or target in invoice_ref
        ):
            # Length-gated: two-character references collide constantly, and a
            # spurious containment hit is worth 20 points of a 100-point score.
            score = 85.0
        else:
            score = float(fuzz.ratio(invoice_ref, target))
        if score > best:
            best, label = score, which

    if not label:
        return None
    return best, f"reference {invoice.po_number!r} vs {label} = {best:.0f}"


def _score_date(
    invoice: InvoiceExtraction, order: OdooPurchaseOrder
) -> tuple[float, str] | None:
    """Reward an invoice dated plausibly after its order.

    Asymmetric on purpose. An invoice normally follows its purchase order by
    days or weeks, so a small positive gap is expected. An invoice dated
    *before* the order is far more suspicious and is penalised harder.
    """
    invoice_date = invoice.order_date_value
    order_date = order.date_order
    if invoice_date is None or order_date is None:
        return None

    days = (invoice_date - order_date).days

    if 0 <= days <= 30:
        score = 100.0
    elif 30 < days <= 90:
        score = 75.0
    elif 90 < days <= 180:
        score = 40.0
    elif -7 <= days < 0:
        score = 60.0  # the vendor dated it early, or a timezone rolled it back
    elif -30 <= days < -7:
        score = 25.0
    else:
        score = 0.0

    direction = "after" if days >= 0 else "before"
    return score, f"invoice dated {abs(days)}d {direction} the order"


def _score_lines(
    invoice: InvoiceExtraction, order: OdooPurchaseOrder
) -> tuple[float, str] | None:
    """Greedy one-to-one assignment of invoice lines to order lines.

    Denominated by `max(len)`, not `min(len)`: an invoice with one line must
    not score 100% against a ten-line order just because that one line matched.
    A partial delivery should surface as a mid score — visibly partial — rather
    than as either a perfect match or a rejection.
    """
    if not invoice.items or not order.lines:
        return None

    order_names = [
        normalise_vendor(line.product_name or line.name) for line in order.lines
    ]
    remaining = set(range(len(order_names)))
    matched = 0

    for item in invoice.items:
        needle = normalise_vendor(item.name)
        if not needle:
            continue

        best_index, best_score = None, 0.0
        for index in remaining:
            if not order_names[index]:
                continue
            score = float(fuzz.token_set_ratio(needle, order_names[index]))
            if score > best_score:
                best_index, best_score = index, score

        # 75 is deliberately permissive: descriptions genuinely differ between
        # a catalogue and a vendor's invoice ("Drill, Heavy Duty 18V" vs
        # "Heavy Duty Industrial Drill") and demanding near-identity here would
        # make this component almost always zero.
        if best_index is not None and best_score >= 75.0:
            remaining.discard(best_index)
            matched += 1

    denominator = max(len(invoice.items), len(order.lines))
    score = 100.0 * matched / denominator
    return score, f"{matched} of {denominator} line(s) matched"


_COMPONENTS = {
    "vendor": _score_vendor,
    "amount": _score_amount,
    "reference": _score_reference,
    "date": _score_date,
    "lines": _score_lines,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_candidate(
    invoice: InvoiceExtraction, order: OdooPurchaseOrder
) -> ScoredCandidate:
    """Score one order. Components that cannot be evaluated are dropped."""
    breakdown: dict[str, float] = {}
    notes: list[str] = []
    weighted_total = 0.0
    weight_used = 0.0

    for name, fn in _COMPONENTS.items():
        result = fn(invoice, order)
        if result is None:
            continue
        component_score, note = result
        weight = WEIGHTS[name]
        breakdown[name] = component_score
        notes.append(note)
        weighted_total += component_score * weight
        weight_used += weight

    # Renormalise over the components that applied, so a missing date does not
    # cost 15 points of a score it was never able to earn.
    score = weighted_total / weight_used if weight_used else 0.0
    return ScoredCandidate(order=order, score=score, breakdown=breakdown, notes=notes)


def rank(
    invoice: InvoiceExtraction,
    orders: list[OdooPurchaseOrder],
    *,
    limit: int = 15,
    floor: float = 35.0,
) -> list[ScoredCandidate]:
    """Score every order and return the plausible ones, best first.

    The floor matters as much as the limit. Padding the shortlist with
    implausible orders does not help the model choose — it gives it more ways
    to be wrong, and every one of them is billed.
    """
    scored = [score_candidate(invoice, order) for order in orders]
    scored.sort(key=lambda c: c.score, reverse=True)
    return [c for c in scored if c.score >= floor][:limit]
