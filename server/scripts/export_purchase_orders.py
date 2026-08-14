"""Export live Odoo purchase orders to JSON.

    python scripts/export_purchase_orders.py
    python scripts/export_purchase_orders.py --all --out fixtures/all_pos.json

Two uses:

  * a snapshot to inspect, or to hand to someone building against this data
  * an offline fixture — point ODOO_FIXTURE_PATH at the output and the whole
    matching pipeline runs without touching Odoo again, which makes iterating
    on the scoring weights fast and free

The output is `OdooPurchaseOrder.model_dump()`, the same shape the live client
returns, so `make_test_invoice.py` reads it unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services.odoo_service import odoo_service  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Ignore the invoice_status filter and take every order in state.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default="fixtures/odoo_purchase_orders.json")
    args = parser.parse_args()

    if args.all:
        # Reaches past the "awaiting a bill" filter. Useful for seeing the whole
        # picture; not what matching should normally consider.
        settings.ODOO_PO_INVOICE_STATUS = "no"

    orders = await odoo_service.fetch_open_purchase_orders(limit=args.limit)

    out = Path(__file__).resolve().parent.parent / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([o.model_dump(mode="json") for o in orders], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    total_lines = sum(len(o.lines) for o in orders)
    print(f"Wrote {len(orders)} purchase order(s), {total_lines} line(s)")
    print(f"  -> {out}  ({out.stat().st_size:,} bytes)\n")

    # A shape summary is more useful than the rows themselves: it says whether
    # this data can actually be matched against.
    with_ref = sum(1 for o in orders if o.partner_ref)
    with_lines = sum(1 for o in orders if o.lines)
    vendors = {o.partner_name for o in orders if o.partner_name}
    amounts = sorted(o.amount_untaxed for o in orders)

    print(f"  distinct vendors     {len(vendors)}")
    print(f"  carry a vendor ref   {with_ref} of {len(orders)}")
    print(f"  have line items      {with_lines} of {len(orders)}")
    if amounts:
        print(
            f"  untaxed range        {amounts[0]:,.2f} … {amounts[-1]:,.2f}"
            f"  (median {amounts[len(amounts) // 2]:,.2f})"
        )

    duplicates = len(amounts) - len(set(amounts))
    if duplicates:
        print(
            f"  !! {duplicates} order(s) share an untaxed amount with another — "
            f"amount alone cannot separate those, which is what the vendor and "
            f"reference components are for"
        )

    print("\nTo run matching against this snapshot instead of live Odoo:")
    print(f"    ODOO_URL=            # blank it out")
    print(f"    ODOO_FIXTURE_PATH={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
