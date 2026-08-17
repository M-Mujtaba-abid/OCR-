"""Build the exact `purchase.order` payload for an invoice — and send nothing.

Read-only against Odoo. It runs the real resolution, prints what would be
created, and stops. That is worth having as its own tool: this is the one place
the system writes to the ERP, and "the vals are right" should be something you
can see before a record exists rather than after.

    python scripts/preview_po.py <invoice-uuid>
    python scripts/preview_po.py --vendor "AJK Restaurants" --item "J5 (lemon)"

The second form needs no database — it resolves the names you give it, which is
how to check a vendor or product that keeps failing without uploading anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252 and this catalogue is half Arabic.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from app.db.session import SessionFactory  # noqa: E402
from app.repositories.match_history_repository import (  # noqa: E402
    MatchHistoryRepository,
)
from app.services import po_creator_service as poc  # noqa: E402


async def _by_name(vendor: str | None, items: list[str]) -> int:
    """Resolve names straight from the command line. No database needed."""
    if vendor:
        match = await poc.resolve_vendor(vendor)
        print(f"vendor {vendor!r}")
        if match:
            print(f"   RESOLVED  {match.name}  (#{match.id}, {match.score:.0f}%)")
        else:
            ranked = poc._rank(
                vendor,
                await poc.odoo_service.search_by_tokens("res.partner", poc._tokens(vendor)),
            )
            print("   REFUSED — closest were:")
            for candidate in ranked[:3]:
                print(f"      {candidate.score:5.1f}  {candidate.name}")
            if not ranked:
                print("      (nothing found for those tokens)")

    for item in items:
        candidates = await poc.product_candidates(item)
        chosen = poc._preselect(candidates)
        print(f"\nitem {item!r}")
        for candidate in candidates:
            mark = " <- preselected" if candidate.id == chosen else ""
            print(f"      {candidate.score:5.1f}  {candidate.name}{mark}")
        if not candidates:
            print("      (nothing found for those tokens)")
        elif chosen is None:
            print("      -> no preselection: the reviewer must choose")
    return 0


async def _by_invoice(invoice_id: str) -> int:
    async with SessionFactory() as db:
        invoice = await MatchHistoryRepository(db).find_by_id(uuid.UUID(invoice_id))
        if invoice is None:
            print(f"No invoice {invoice_id}")
            return 1

        preview = await poc.build_preview(invoice)

    vendor = preview["vendor"]
    print(f"invoice   {invoice.file_name}")
    print(f"vendor    {preview['vendor_name']!r}")
    print(
        f"resolved  {vendor['name']} (#{vendor['id']}, {vendor['score']:.0f}%)"
        if vendor
        else "resolved  REFUSED — no purchase order can be raised from here"
    )
    print(f"date      {preview['order_date']}\n")

    unchosen = 0
    lines: list[dict[str, object]] = []
    for line in preview["lines"]:  # type: ignore[union-attr]
        print(f"line {line['line_no']}: {line['description']!r}")
        for candidate in line["candidates"]:
            mark = (
                " <- preselected"
                if candidate["id"] == line["preselected_product_id"]
                else ""
            )
            print(f"      {candidate['score']:5.1f}  {candidate['name']}{mark}")
        if line["preselected_product_id"] is None:
            unchosen += 1
            print("      -> the reviewer must choose")
        else:
            lines.append(
                {
                    "product_id": line["preselected_product_id"],
                    "name": line["description"],
                    "product_qty": line["quantity"],
                    "price_unit": line["unit_price"],
                }
            )
        print()

    if not vendor or unchosen:
        print(
            f"NOT CREATABLE unattended: "
            f"{'no vendor' if not vendor else ''}"
            f"{' and ' if not vendor and unchosen else ''}"
            f"{f'{unchosen} line(s) need a choice' if unchosen else ''}"
        )
        return 0

    print("would send to purchase.order.create:")
    print(
        json.dumps(
            {
                "partner_id": vendor["id"],
                "date_order": poc._as_odoo_datetime(preview["order_date"]),  # type: ignore[arg-type]
                "order_line": [[0, 0, line] for line in lines],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print("\n(nothing was sent)")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("invoice_id", nargs="?", help="Invoice UUID to preview.")
    parser.add_argument("--vendor", help="Resolve this vendor name and stop.")
    parser.add_argument(
        "--item", action="append", default=[], help="Resolve this item name (repeatable)."
    )
    args = parser.parse_args()

    if args.vendor or args.item:
        return await _by_name(args.vendor, args.item)
    if args.invoice_id:
        return await _by_invoice(args.invoice_id)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
