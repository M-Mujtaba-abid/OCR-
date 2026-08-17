"""Measure what the reranker is billed for, per invoice.

Nothing is sent to Mistral: the prompt is built exactly as `_rerank` builds it
and then measured. That makes "this change made it cheaper" a number rather
than an opinion, and it costs nothing to re-run after every change.

Odoo IS queried, because the cost is dominated by real candidates — how many
clear the floor, how far apart they score, and how long the vendor names are.
A synthetic fixture would measure the wrong thing.

    python scripts/measure_rerank_cost.py
    python scripts/measure_rerank_cost.py --candidates   # list what was sent

The token figures are an estimate (~3.5 characters per token) and are there for
ratios, not for reconciling an invoice from Mistral. Arabic product names run
denser than that, so treat the absolute numbers as a floor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252 and this data is full of Arabic product
# names — without this the script dies while printing its own results.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from app.core.config import settings  # noqa: E402
from app.schemas.extraction import InvoiceExtraction  # noqa: E402
from app.services import matching_engine  # noqa: E402
from app.services.match_service import (  # noqa: E402
    RERANK_PROMPT,
    _beyond_argument,
    _shortlist_for_prompt,
    _orders_to_consider,
)

CHARS_PER_TOKEN = 3.5

#: Real documents from this deployment, kept as the yardstick. Add to these as
#: awkward invoices turn up — a case that once cost too much is worth keeping.
SAMPLES: dict[str, dict[str, object]] = {
    "handwritten note, no clear winner": {
        "vendor_name": "AJK Retardant",
        "po_number": None,
        "order_date": "2026-08-17",
        "currency": "AED",
        "items": [
            {"name": "Egg Plant (C. Int.)", "quantity": 1, "unit_price": 1.0, "subtotal": 1.0}
        ],
        "untaxed_amount": 1.0,
        "tax_amount": 0.05,
        "total_amount": 1.05,
    },
    "handwritten note, one runaway leader": {
        "vendor_name": "AJK Restaurants",
        "po_number": None,
        "order_date": "2026-07-22",
        "currency": "AED",
        "items": [
            {"name": "J5 (lemon)", "quantity": 1, "unit_price": 7.02, "subtotal": 7.02}
        ],
        "untaxed_amount": 7.02,
        "tax_amount": 0.0,
        "total_amount": 7.02,
    },
    # Quotes an order that is still awaiting a bill — the one case the fast
    # path is allowed to settle without asking the model. The same invoice
    # against an already-invoiced order deliberately does NOT qualify: that is
    # the possible-duplicate case and it always gets a second opinion.
    "vendor quoting the PO number": {
        "vendor_name": "Berry Mount Vegetables And Fruit Trading",
        "po_number": "P01658",
        "order_date": "2026-07-25",
        "currency": "AED",
        "items": [
            {"name": "Assorted Flower", "quantity": 1, "unit_price": 50000.0,
             "subtotal": 50000.0}
        ],
        "untaxed_amount": 50000.0,
        "tax_amount": 2500.0,
        "total_amount": 52500.0,
    },
}


def _prompt_chars(extraction: InvoiceExtraction, shortlist: list) -> int:
    """The payload `_rerank` would send, measured rather than guessed."""
    payload = {
        "invoice": {
            "vendor_name": extraction.vendor_name,
            "reference": extraction.po_number,
            "order_date": extraction.order_date,
            "currency": extraction.currency,
            "untaxed_amount": round(extraction.untaxed_amount, 2),
            "total_amount": round(extraction.total_amount, 2),
            "items": [item.model_dump() for item in extraction.items[:25]],
        },
        "candidates": [
            {
                **c.order.for_prompt(item_limit=settings.MATCH_PROMPT_ITEM_CAP),
                "score": round(c.score, 1),
                "prefilter": " ".join(
                    f"{name[0]}{round(value)}" for name, value in c.breakdown.items()
                ),
            }
            for c in shortlist
        ],
    }
    return len(RERANK_PROMPT) + len(json.dumps(payload, ensure_ascii=False))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates", action="store_true", help="Print the candidates sent."
    )
    args = parser.parse_args()

    print(
        f"margin={settings.MATCH_PROMPT_MARGIN:.0f} "
        f"min={settings.MATCH_PROMPT_MIN} "
        f"items={settings.MATCH_PROMPT_ITEM_CAP} "
        f"auto_accept={settings.MATCH_AUTO_ACCEPT_SCORE:.0f}"
        f"/{settings.MATCH_AUTO_ACCEPT_MARGIN:.0f}\n"
    )

    billed = 0
    for label, raw in SAMPLES.items():
        extraction = InvoiceExtraction.model_validate(raw)
        orders = await _orders_to_consider(extraction)
        ranked = matching_engine.rank(
            extraction,
            orders,
            limit=settings.MATCH_CANDIDATE_LIMIT,
            floor=settings.MATCH_SCORE_FLOOR,
        )

        print(f"{label}")
        if not ranked:
            print("   nothing cleared the floor — no rerank call\n")
            continue

        settled = _beyond_argument(ranked)
        if settled is not None:
            print(
                f"   {len(orders)} orders -> {len(ranked)} shortlisted -> "
                f"NO CALL ({settled.order.name} scored {settled.score:.1f} on an "
                f"exact reference)\n"
            )
            continue

        shortlist = _shortlist_for_prompt(ranked)
        chars = _prompt_chars(extraction, shortlist)
        billed += chars
        print(
            f"   {len(orders)} orders -> {len(ranked)} shortlisted -> "
            f"{len(shortlist)} described"
        )
        print(
            f"   scores {[round(c.score, 1) for c in ranked[:6]]}"
            f"{' …' if len(ranked) > 6 else ''}"
        )
        print(f"   prompt {chars:,} chars  ≈ {chars / CHARS_PER_TOKEN:,.0f} tokens\n")

        if args.candidates:
            for c in shortlist:
                print(f"      {c.order.name:10} {c.score:5.1f}  {c.order.partner_name}")
            print()

    print(f"billed across {len(SAMPLES)} sample(s): ≈ {billed / CHARS_PER_TOKEN:,.0f} input tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
