"""Generate a realistic purchase-order fixture.

Exists so the matching pipeline can be exercised end to end before Odoo
credentials are available. The output is the exact shape
`OdooPurchaseOrder.model_dump()` produces, so when the real connection is wired
up nothing downstream changes — only where the list comes from.

    python scripts/seed_purchase_orders.py
    python scripts/seed_purchase_orders.py --count 40 --out fixtures/pos.json

The data is deliberately awkward. A fixture where every order has a distinct
vendor and a distinct amount would make the matcher look perfect and prove
nothing, so this includes the cases that actually break matching:

  * the same vendor with several open orders of similar value
  * two different vendors with near-identical totals
  * a vendor whose legal form is written differently from the invoice
  * orders far enough in the past that the date component should reject them
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

VENDORS = [
    ("Acme Tools Limited", "AED"),
    ("Gulf Industrial Supplies LLC", "AED"),
    ("Karachi Steel & Fabrication (Pvt) Ltd", "PKR"),
    ("Emirates Safety Equipment Trading", "AED"),
    ("Nova Electronics FZE", "USD"),
    ("Al Madina Hardware Est", "AED"),
    ("Precision Bearings International", "USD"),
    ("Sunrise Packaging Company", "PKR"),
]

CATALOGUE = [
    ("Heavy Duty Industrial Drill 18V", 150.00),
    ("Carbide Drill Bit Set (25 pc)", 12.50),
    ("Safety Goggles - Anti Fog", 8.75),
    ("Hydraulic Floor Jack 3T", 420.00),
    ("Stainless Steel Sheet 2mm", 68.40),
    ("Industrial Air Compressor 50L", 890.00),
    ("Welding Rod E6013 (5kg)", 34.00),
    ("Torque Wrench 1/2in 200Nm", 165.00),
    ("Cut-off Wheel 14in", 6.25),
    ("Protective Work Gloves (pair)", 4.90),
    ("LED Work Light 50W", 72.00),
    ("Ball Bearing 6205-2RS", 3.75),
    ("Corrugated Carton 600x400", 1.85),
    ("Digital Multimeter CAT III", 118.00),
    ("Angle Grinder 900W", 205.00),
]


def build(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    today = dt.date.today()
    orders: list[dict] = []

    for index in range(count):
        vendor, currency = VENDORS[index % len(VENDORS)]

        # Spread orders over six months. The oldest should fall out of the
        # date window, which is the point — a matcher that ignores dates would
        # score them the same as last week's.
        age_days = rng.choice([2, 5, 9, 14, 21, 30, 45, 70, 110, 160])
        order_date = today - dt.timedelta(days=age_days)

        lines = []
        for line_no, (name, unit) in enumerate(
            rng.sample(CATALOGUE, rng.randint(1, 5)), start=1
        ):
            qty = float(rng.choice([1, 2, 3, 4, 5, 10, 12, 20, 25, 50, 100]))
            price = round(unit * rng.uniform(0.95, 1.05), 2)
            lines.append(
                {
                    "id": (index + 1) * 100 + line_no,
                    "order_id": index + 1,
                    "name": name,
                    "product_id": CATALOGUE.index((name, unit)) + 1000,
                    "product_name": name,
                    "product_qty": qty,
                    "qty_invoiced": 0.0,
                    "price_unit": price,
                    "price_subtotal": round(qty * price, 2),
                    "uom": "Units",
                }
            )

        untaxed = round(sum(line["price_subtotal"] for line in lines), 2)
        tax = round(untaxed * 0.05, 2)

        orders.append(
            {
                "id": index + 1,
                "name": f"PO{order_date:%Y%m}{index + 1:04d}",
                "partner_id": VENDORS.index((vendor, currency)) + 500,
                "partner_name": vendor,
                # Two thirds carry a vendor reference. The rest exercise the
                # path where the reference component cannot be scored at all.
                "partner_ref": (
                    f"REF-{rng.randint(10000, 99999)}" if rng.random() < 0.66 else None
                ),
                "date_order": order_date.isoformat(),
                "amount_untaxed": untaxed,
                "amount_tax": tax,
                "amount_total": round(untaxed + tax, 2),
                "currency": currency,
                "state": "purchase",
                "invoice_status": "to invoice",
                "lines": lines,
            }
        )

    # The hard case, added deliberately: a different vendor with the SAME
    # untaxed total and the same goods as an existing order. Amount and line
    # items alone cannot separate these two — only the vendor can, which is
    # exactly the decision worth testing.
    twin = dict(orders[0])
    twin["id"] = count + 1
    twin["name"] = f"PO{today:%Y%m}{count + 1:04d}"
    twin["partner_name"] = "Gulf Industrial Supplies LLC"
    twin["partner_id"] = 501
    twin["partner_ref"] = None
    twin["lines"] = [
        {**line, "id": (count + 1) * 100 + i, "order_id": count + 1}
        for i, line in enumerate(orders[0]["lines"], start=1)
    ]
    orders.append(twin)

    return orders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument("--out", default="fixtures/purchase_orders.json")
    args = parser.parse_args()

    orders = build(args.count, args.seed)

    out = Path(__file__).resolve().parent.parent / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(orders, indent=2), encoding="utf-8")

    print(f"Wrote {len(orders)} purchase orders to {out}")
    print()
    print(f"{'PO number':<16} {'vendor':<40} {'untaxed':>12}  {'date':<12} lines")
    print("-" * 92)
    for order in orders:
        print(
            f"{order['name']:<16} {order['partner_name'][:38]:<40} "
            f"{order['amount_untaxed']:>12,.2f}  {order['date_order']:<12} "
            f"{len(order['lines'])}"
        )
    print()
    print("Point the server at it with:")
    print(f"    ODOO_FIXTURE_PATH={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
