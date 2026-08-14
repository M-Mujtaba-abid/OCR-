"""Generate a test invoice PDF from a purchase order in the fixture.

    # list what is available
    python scripts/make_test_invoice.py --list

    # an invoice that should match PO202608010003
    python scripts/make_test_invoice.py --po PO202608010003

    # a random one
    python scripts/make_test_invoice.py --random

    # one that should NOT match anything
    python scripts/make_test_invoice.py --unmatchable

The PDF is written with no dependencies — a hand-assembled PDF 1.7 with a
Flate-compressed content stream. reportlab would be a 10 MB dependency to
produce a page of text.

The generated invoice is deliberately NOT a copy of the purchase order. Real
vendors write their own legal form, reword line descriptions, apply their own
tax rate and quote their own invoice number. An invoice that were a byte-copy
of its PO would make the matcher look perfect while testing nothing, so this
applies those distortions on purpose:

  * "Limited" becomes "Ltd", "(Pvt) Ltd" becomes "Pvt Limited", and so on
  * line descriptions are reordered and reworded
  * the vendor's own invoice number is used, with the PO quoted separately
  * amounts stay honest — that is the signal matching leans on hardest
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = Path(
    __import__("os").environ.get("PO_FIXTURE")
    or Path(__file__).resolve().parent.parent / "fixtures" / "purchase_orders.json"
)

#: How a vendor might write its own name differently from the ERP record.
LEGAL_FORM_REWRITES = [
    ("Limited", "Ltd."),
    ("(Pvt) Ltd", "Pvt Limited"),
    ("LLC", "L.L.C."),
    ("FZE", "F.Z.E"),
    ("Est", "Establishment"),
    ("Company", "Co."),
    ("International", "Intl."),
    ("Trading", "Trdg."),
]

#: How a vendor might reword a catalogue description.
def reword(name: str, rng: random.Random) -> str:
    parts = name.split(" - ")
    base = parts[0]

    style = rng.choice(["comma", "prefix", "asis", "upper"])
    if style == "comma" and " " in base:
        words = base.split()
        return f"{words[-1]}, {' '.join(words[:-1])}"
    if style == "prefix":
        return f"Supply of {base}"
    if style == "upper":
        return base.upper()
    return base


def rewrite_vendor(name: str, rng: random.Random) -> str:
    for needle, replacement in LEGAL_FORM_REWRITES:
        if needle in name:
            return name.replace(needle, replacement)
    return name.upper() if rng.random() < 0.5 else name


# ---------------------------------------------------------------------------
# PDF writing
# ---------------------------------------------------------------------------
def escape(text: str) -> str:
    """PDF strings are parenthesised, so those three characters must escape."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_pdf(path: Path, lines: list[tuple[int, int, int, str]]) -> None:
    content = "BT\n" + "".join(
        f"/F1 {size} Tf 1 0 0 1 {x} {y} Tm ({escape(text)}) Tj\n"
        for x, y, size, text in lines
    ) + "ET\n"
    stream = zlib.compress(content.encode("latin-1", "replace"))

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"

    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )

    path.write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# Invoice layout
# ---------------------------------------------------------------------------
def build_invoice(order: dict, rng: random.Random) -> tuple[list, dict]:
    vendor = rewrite_vendor(order["partner_name"], rng)
    order_date = dt.date.fromisoformat(order["date_order"])
    # Vendors bill days to weeks after the order, not the same day.
    invoice_date = order_date + dt.timedelta(days=rng.randint(3, 21))
    invoice_no = f"INV-{rng.randint(1000, 9999)}/{invoice_date:%y}"

    items = list(order["lines"])
    rng.shuffle(items)

    y = 748
    lines: list[tuple[int, int, int, str]] = [
        (60, 768, 17, vendor),
        (60, y, 9, "Sales Office · sales@" + vendor.split()[0].lower().strip(".,") + ".example"),
    ]
    y -= 34
    lines += [
        (60, y, 15, "TAX INVOICE"),
        (400, y, 10, f"Invoice No: {invoice_no}"),
    ]
    y -= 18
    lines += [(400, y, 10, f"Invoice Date: {invoice_date:%Y-%m-%d}")]
    y -= 16
    # The reference the matcher should find. Written the way vendors write it —
    # labelled, not bare.
    lines += [(400, y, 10, f"Your PO: {order['name']}")]
    if order.get("partner_ref"):
        y -= 14
        lines += [(400, y, 9, f"Our Ref: {order['partner_ref']}")]

    y -= 40
    lines += [
        (60, y, 10, "Description"),
        (330, y, 10, "Qty"),
        (400, y, 10, "Unit Price"),
        (500, y, 10, "Amount"),
    ]
    y -= 6
    lines += [(60, y, 8, "_" * 96)]
    y -= 18

    for line in items:
        lines += [
            (60, y, 9, reword(line["name"], rng)[:46]),
            (330, y, 9, f"{line['product_qty']:g}"),
            (400, y, 9, f"{line['price_unit']:,.2f}"),
            (500, y, 9, f"{line['price_subtotal']:,.2f}"),
        ]
        y -= 16

    # The vendor's own tax rate, not the ERP's. Untaxed stays exact — that is
    # what a purchase order's amount_untaxed is compared against.
    untaxed = order["amount_untaxed"]
    tax_rate = rng.choice([0.05, 0.05, 0.15, 0.17, 0.0])
    tax = round(untaxed * tax_rate, 2)

    y -= 14
    lines += [(60, y, 8, "_" * 96)]
    y -= 22
    lines += [
        (380, y, 10, "Subtotal:"),
        (500, y, 10, f"{untaxed:,.2f}"),
    ]
    y -= 16
    lines += [
        (380, y, 10, f"Tax ({tax_rate:.0%}):"),
        (500, y, 10, f"{tax:,.2f}"),
    ]
    y -= 20
    lines += [
        (380, y, 12, "TOTAL DUE:"),
        (500, y, 12, f"{untaxed + tax:,.2f} {order['currency']}"),
    ]

    expected = {
        "po_id": order["id"],
        "po_number": order["name"],
        "vendor_on_po": order["partner_name"],
        "vendor_on_invoice": vendor,
        "invoice_no": invoice_no,
        "invoice_date": invoice_date.isoformat(),
        "untaxed": untaxed,
        "tax": tax,
        "total": round(untaxed + tax, 2),
        "currency": order["currency"],
        "line_count": len(items),
    }
    return lines, expected


def build_unmatchable(rng: random.Random) -> tuple[list, dict]:
    """An invoice from a vendor and for goods that appear in no purchase order."""
    invoice_date = dt.date.today() - dt.timedelta(days=rng.randint(1, 10))
    invoice_no = f"INV-{rng.randint(1000, 9999)}"
    untaxed = 1875.40
    tax = round(untaxed * 0.05, 2)

    lines = [
        (60, 768, 17, "Zenith Office Interiors WLL"),
        (60, 748, 9, "accounts@zenithinteriors.example"),
        (60, 714, 15, "TAX INVOICE"),
        (400, 714, 10, f"Invoice No: {invoice_no}"),
        (400, 696, 10, f"Invoice Date: {invoice_date:%Y-%m-%d}"),
        (60, 650, 10, "Description"),
        (330, 650, 10, "Qty"),
        (400, 650, 10, "Unit Price"),
        (500, 650, 10, "Amount"),
        (60, 644, 8, "_" * 96),
        (60, 626, 9, "Executive Mesh Chair"), (330, 626, 9, "8"),
        (400, 626, 9, "185.00"), (500, 626, 9, "1,480.00"),
        (60, 610, 9, "Cable Management Tray 1200mm"), (330, 610, 9, "12"),
        (400, 610, 9, "32.95"), (500, 610, 9, "395.40"),
        (60, 580, 8, "_" * 96),
        (380, 558, 10, "Subtotal:"), (500, 558, 10, f"{untaxed:,.2f}"),
        (380, 542, 10, "Tax (5%):"), (500, 542, 10, f"{tax:,.2f}"),
        (380, 520, 12, "TOTAL DUE:"), (500, 520, 12, f"{untaxed + tax:,.2f} AED"),
    ]
    return lines, {
        "po_id": None,
        "po_number": None,
        "vendor_on_invoice": "Zenith Office Interiors WLL",
        "invoice_no": invoice_no,
        "untaxed": untaxed,
        "tax": tax,
        "total": round(untaxed + tax, 2),
        "currency": "AED",
        "note": "No purchase order matches this vendor or these goods.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--po", help="Purchase order number to invoice against.")
    parser.add_argument("--random", action="store_true", help="Pick one at random.")
    parser.add_argument("--unmatchable", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--out", default="fixtures/invoices")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()

    if not FIXTURE.exists():
        print(f"No fixture at {FIXTURE}. Run scripts/seed_purchase_orders.py first.")
        return 1

    orders = json.loads(FIXTURE.read_text(encoding="utf-8"))

    if args.list:
        print(f"{'PO number':<16} {'vendor':<40} {'untaxed':>12}  date")
        print("-" * 86)
        for order in orders:
            print(
                f"{order['name']:<16} {order['partner_name'][:38]:<40} "
                f"{order['amount_untaxed']:>12,.2f}  {order['date_order']}"
            )
        return 0

    rng = random.Random(args.seed)
    out_dir = Path(__file__).resolve().parent.parent / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.unmatchable:
        lines, expected = build_unmatchable(rng)
        path = out_dir / "invoice-unmatchable.pdf"
    else:
        if args.random:
            order = rng.choice(orders)
        elif args.po:
            order = next((o for o in orders if o["name"] == args.po), None)
            if order is None:
                print(f"No purchase order named {args.po}. Use --list to see them.")
                return 1
        else:
            parser.error("Give --po, --random, --unmatchable or --list.")

        lines, expected = build_invoice(order, rng)
        path = out_dir / f"invoice-{order['name']}.pdf"

    write_pdf(path, lines)

    print(f"Wrote {path}  ({path.stat().st_size:,} bytes)\n")
    print("What the matcher should find:")
    for key, value in expected.items():
        print(f"  {key:<20} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
