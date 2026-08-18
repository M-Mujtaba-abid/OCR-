"""Odoo integration over XML-RPC.

`xmlrpc.client` is stdlib and **fully blocking** — a single call would stall the
event loop for the whole round trip, and an Odoo instance on another continent
makes that hundreds of milliseconds per request. So the blocking work lives in
`_BlockingOdooClient` and every entry point offloads it through
`anyio.to_thread.run_sync`, exactly as `core/storage.py` does for boto3.

The one performance decision that matters here is batching. Odoo returns a
purchase order's lines as a list of ids; fetching them per order is an N+1 that
turns 200 orders into 201 network round trips. Every id is collected first and
read in one call instead.
"""

from __future__ import annotations

import base64
import datetime as dt
import functools
import json
import threading
import time
import xmlrpc.client
from pathlib import Path
from typing import Any, NamedTuple

import anyio.to_thread

from app.core.config import settings
from app.core.exceptions import (
    NothingToBillError,
    OdooAuthError,
    OdooError,
    OdooNotConfiguredError,
    OdooRefusedError,
    ReceiptNotPossibleError,
)
from app.lib.logging import get_logger
from app.schemas.odoo import (
    OdooAttachment,
    OdooCreatedBill,
    OdooCreatedOrder,
    OdooExistingBill,
    OdooPurchaseOrder,
    OdooPurchaseOrderLine,
    OdooReceiptResult,
    _relation_id,
    _relation_name,
)

logger = get_logger(__name__)

#: Read off `purchase.order`. Deliberately explicit rather than fetching
#: everything: an Odoo order has 80+ fields and most are irrelevant here.
PO_FIELDS = [
    "name",
    "partner_id",
    "partner_ref",
    "date_order",
    "amount_untaxed",
    "amount_tax",
    "amount_total",
    "currency_id",
    "state",
    "invoice_status",
    "order_line",
    # Billing reads both: `invoice_ids` is diffed around
    # `action_create_invoice` to find the bill it made, and `company_id`
    # disambiguates the journal in a multi-company database.
    "invoice_ids",
    "company_id",
]

PO_LINE_FIELDS = [
    "order_id",
    "name",
    "product_id",
    "product_qty",
    # Matching only ever needed "ordered" and "already billed". Billing needs
    # to know what has arrived and what Odoo would invoice right now, and
    # `display_type` to tell a heading apart from goods.
    "qty_received",
    "qty_invoiced",
    "qty_to_invoice",
    "display_type",
    "price_unit",
    "price_subtotal",
    "price_tax",
    "price_total",
    "product_uom",
]

#: Read off `stock.move` when receiving. `picking_id` is what gets validated,
#: `purchase_line_id` is the authoritative link back to the order line — the
#: `stock.picking.purchase_id` related field is not reliably searchable.
STOCK_MOVE_FIELDS = [
    "picking_id",
    "product_id",
    "purchase_line_id",
    "product_uom",
    "product_uom_qty",
    "state",
    "has_tracking",
]

#: Read off `account.move` when looking for an existing bill.
BILL_FIELDS = ["name", "ref", "state", "amount_total", "invoice_origin", "invoice_date"]

#: Odoo's default precision for "Product Unit of Measure" is 3 decimals, so
#: anything under this is float noise rather than a quantity. A constant rather
#: than a read of `decimal.precision`: that is a per-request round trip for a
#: setting nobody changes.
QTY_TOLERANCE = 0.001

#: Odoo states an order must be in before it can carry a bill. A draft RFQ has
#: no committed lines, so `purchase_line_id` on a bill line would be meaningless.
BILLABLE_PO_STATES = frozenset({"purchase", "done"})


class _BlockingOdooClient:
    """The synchronous XML-RPC client. Never called from the event loop."""

    def __init__(self) -> None:
        base = settings.odoo_base_url
        # allow_none is not optional: Odoo returns nulls, and without this
        # xmlrpc raises TypeError while marshalling them.
        self._common = xmlrpc.client.ServerProxy(
            f"{base}/xmlrpc/2/common", allow_none=True
        )
        self._models = xmlrpc.client.ServerProxy(
            f"{base}/xmlrpc/2/object", allow_none=True
        )
        self._uid: int | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ auth
    def authenticate(self, *, force: bool = False) -> int:
        """Return the uid, authenticating once per process.

        Odoo's `authenticate` is a full login round trip, so caching it turns
        every subsequent call into one request instead of two.
        """
        if self._uid is not None and not force:
            return self._uid

        with self._lock:
            if self._uid is not None and not force:
                return self._uid

            try:
                uid = self._common.authenticate(
                    settings.ODOO_DB,
                    settings.ODOO_USERNAME,
                    settings.ODOO_API_KEY.get_secret_value(),
                    {},
                )
            except Exception as exc:
                logger.exception("Odoo authentication call failed")
                raise OdooError() from exc

            # Odoo answers a bad login with `False`, not an exception.
            if not uid:
                raise OdooAuthError()

            self._uid = int(uid)
            logger.info("Authenticated with Odoo as uid=%s", self._uid)
            return self._uid

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call a model method, re-authenticating once on a session fault."""
        uid = self.authenticate()
        try:
            return self._call(uid, model, method, args, kwargs)
        except xmlrpc.client.Fault as fault:
            # A session that expired server-side surfaces as an access fault.
            # One silent re-auth is worth it; a second would be a loop.
            if _is_session_fault(fault):
                logger.info("Odoo session expired — re-authenticating")
                uid = self.authenticate(force=True)
                return self._call(uid, model, method, args, kwargs)
            raise

    def _call(
        self,
        uid: int,
        model: str,
        method: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        return self._models.execute_kw(
            settings.ODOO_DB,
            uid,
            settings.ODOO_API_KEY.get_secret_value(),
            model,
            method,
            list(args),
            kwargs,
        )


def _is_session_fault(fault: xmlrpc.client.Fault) -> bool:
    text = (fault.faultString or "").lower()
    return "access denied" in text or "session expired" in text


#: Odoo's XML-RPC fault codes. 1 is a server error and the fault string is a
#: full traceback; 2 is a `UserError` and the fault string is just the message.
#: The two need opposite handling, so they are named rather than compared to
#: bare integers at the call site.
_FAULT_SERVER_ERROR = 1
_FAULT_USER_ERROR = 2

#: A `UserError` message is written for a person, but it is still Odoo's text
#: arriving over the wire — capped so a pathological one cannot fill a log line
#: or a toast.
_MAX_REFUSAL_CHARS = 400


def odoo_refusal(fault: xmlrpc.client.Fault) -> str | None:
    """Odoo's own explanation, when it declined rather than broke.

    Pure, so the classification can be tested without an Odoo. Returns None for
    anything that is not a `UserError` — including tracebacks, which must never
    reach a client because they carry the database name and internal paths.

    Odoo sends these as several short lines ("… cannot be validated because …"
    / "Please complete the inspection first"). They are joined into one
    sentence rather than kept as a block, because the destination is a toast.
    """
    if fault.faultCode != _FAULT_USER_ERROR:
        return None
    lines = [line.strip() for line in (fault.faultString or "").splitlines()]
    message = " ".join(line for line in lines if line)
    if not message:
        return None
    return message[:_MAX_REFUSAL_CHARS]


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------
_client: _BlockingOdooClient | None = None
_client_lock = threading.Lock()


def _get_client() -> _BlockingOdooClient:
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client
        if not settings.is_odoo_configured:
            raise OdooNotConfiguredError()
        _client = _BlockingOdooClient()
        return _client


def reset_odoo_client() -> None:
    """Drop the cached client, its uid and the probed field sets.

    The capabilities go too: they describe a particular database's schema, and
    pointing at a different Odoo without clearing them would carry one
    deployment's field names into another's.
    """
    global _client
    with _client_lock:
        _client = None
    with _caps_lock:
        _caps.clear()


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Offload a blocking call, translating faults into typed errors."""
    try:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    except (OdooAuthError, OdooNotConfiguredError):
        raise
    except xmlrpc.client.Fault as fault:
        if _is_session_fault(fault):
            raise OdooAuthError() from fault
        # Odoo declining is not Odoo failing. A UserError arrives as fault code
        # 2 with a message meant for a person and no traceback — "pass the
        # quality inspection first", "the period is locked" — and answering
        # that with "temporarily unavailable, please try again" tells the
        # reviewer to do the one thing that cannot possibly help.
        refusal = odoo_refusal(fault)
        if refusal is not None:
            logger.info("Odoo refused the operation: %s", refusal)
            raise OdooRefusedError(refusal) from fault
        # Anything else is a traceback, and it can carry the database name and
        # internal model paths. Log it, never return it.
        logger.error("Odoo fault: %s", fault.faultString)
        raise OdooError() from fault
    except Exception as exc:
        logger.exception("Odoo call failed (%s)", type(exc).__name__)
        raise OdooError() from exc


# ---------------------------------------------------------------------------
# Fetch cache
# ---------------------------------------------------------------------------
# A twenty-file upload matches twenty invoices against the same Odoo, seconds
# apart, and each one re-read several hundred orders and a thousand lines. The
# orders had not changed between the first file and the twentieth; the queue
# simply waited for the same answer twenty times.
#
# Monotonic clock, not wall time: a clock adjustment must not make an entry
# look hours old or immortal.
_cache: dict[tuple[Any, ...], tuple[float, list[OdooPurchaseOrder]]] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple[Any, ...]) -> list[OdooPurchaseOrder] | None:
    ttl = settings.ODOO_FETCH_CACHE_SECONDS
    if ttl <= 0:
        return None
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None or time.monotonic() - entry[0] > ttl:
            return None
        # A copy: callers attach lines and are free to mutate what they get,
        # and a shared list would carry one invoice's changes into the next.
        logger.debug("Odoo fetch served from cache (%s)", key[0])
        return list(entry[1])


def _remember(key: tuple[Any, ...], orders: list[OdooPurchaseOrder]) -> None:
    if settings.ODOO_FETCH_CACHE_SECONDS <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.monotonic(), list(orders))


def clear_fetch_cache() -> None:
    """Forget every cached fetch. For tests and for a forced refresh."""
    with _cache_lock:
        _cache.clear()


def match_recent_draft(
    rows: list[dict[str, Any]], expected_untaxed: float
) -> dict[str, Any] | None:
    """A draft order already standing for this value, if one is.

    Pure, so the rule can be tested without Odoo. Compared on the untaxed
    total because that is what the caller can predict before creating —
    quantity times price, before Odoo applies each product's own tax.
    """
    for row in rows:
        if abs(float(row.get("amount_untaxed") or 0.0) - expected_untaxed) <= 0.01:
            return row
    return None


# ---------------------------------------------------------------------------
# Capability probe
#
# Odoo 17 renamed `stock.move.quantity_done` to `quantity` and added a `picked`
# boolean. On 17 a write to `quantity_done` raises; on 16 a write to `picked`
# raises. There is no forgiving order to try them in, so the dialect has to be
# established before the first write.
#
# `fields_get` rather than `common.version()`: the version string is "16.0",
# "17.0+e" or "saas~16.4" depending on hosting, and a backported or customised
# 16 can carry `picked` while still reporting 16.0. What matters is what fields
# this database actually has, and that is the question `fields_get` answers.
# ---------------------------------------------------------------------------
#: Asked for by name so the reply is a handful of entries rather than the 200+
#: a bare `fields_get` returns. Fields that do not exist are silently omitted,
#: which is exactly the signal wanted.
_PROBES: dict[str, list[str]] = {
    "stock.move": ["quantity", "picked", "quantity_done", "has_tracking"],
    "account.move": ["payment_state", "invoice_date", "ref"],
}

#: A schema, not data. It cannot change without an Odoo upgrade, which restarts
#: Odoo — so this is cached for the life of the process rather than under the
#: TTL that guards `_cache`, where a 60-second expiry would keep re-asking a
#: question whose answer never moves.
_caps: dict[str, frozenset[str]] = {}
_caps_lock = threading.Lock()


class StockQtyDialect(NamedTuple):
    """How to tell this Odoo that a stock move was received."""

    #: "quantity" on 17+, "quantity_done" on <=16.
    qty_field: str
    #: 17+ only, and not cosmetic — see `stock_qty_dialect`.
    writes_picked: bool


def stock_qty_dialect(fields_present: frozenset[str]) -> StockQtyDialect:
    """Which done-quantity dialect these fields imply.

    Newest first, deliberately: on 17 both `quantity` and `picked` exist, and
    16's `quantity_done` does not, so there is no ambiguity in that order.

    `picked` is load-bearing on 17. `_check_immediate` treats a picking where
    NO move is picked as "the user meant the whole demand" and answers
    `button_validate` with the immediate-transfer wizard — whose `process()`
    receives all 100. Writing the flag is what makes 50 mean 50.
    """
    if "quantity" in fields_present and "picked" in fields_present:
        return StockQtyDialect("quantity", True)
    if "quantity_done" in fields_present:
        return StockQtyDialect("quantity_done", False)
    raise OdooError(
        "This Odoo's stock.move has neither `quantity_done` nor `quantity`, so "
        "goods receipts cannot be recorded automatically."
    )


# ---------------------------------------------------------------------------
# Billing — pure decision logic
#
# Everything here is a rule rather than a round trip, and every one of them is
# testable against dict literals with no Odoo at all. That is deliberate: this
# is where the arithmetic that decides how much a vendor gets paid lives, and
# it should not need a network to prove correct.
# ---------------------------------------------------------------------------
def remaining_to_bill(line: dict[str, Any]) -> float:
    """How much of this order line has not been billed yet.

    `product_qty - qty_invoiced`, NOT `qty_to_invoice`. The two disagree on
    purpose. `qty_to_invoice` answers "how much would Odoo bill right now",
    which under the default bill-control policy is capped by what has arrived —
    a legitimate reason to refuse, but a different one. Over-billing is an
    accounting error against what was ORDERED, and that is the ceiling worth
    naming in a refusal.

    `qty_invoiced` counts draft bills as well as posted ones, because Odoo's
    `_compute_qty_invoiced` filters only on `state != 'cancel'`. That is what
    makes this guard survive a first attempt that created a bill and then
    failed on the way back — the second attempt sees the first one's 50.

    Floored at zero: a vendor credit note can push `qty_invoiced` past
    `product_qty`, and a negative "remaining" would read as a licence to bill.
    """
    if line.get("display_type"):
        return 0.0
    ordered = float(line.get("product_qty") or 0.0)
    invoiced = float(line.get("qty_invoiced") or 0.0)
    return max(0.0, ordered - invoiced)


class OverBilledLine(NamedTuple):
    """One line that would be billed past what the order has left."""

    po_line_id: int
    label: str
    requested: float
    remaining: float


def over_billed_lines(
    lines: list[dict[str, Any]],
    approved: dict[int, float],
    *,
    tolerance: float = QTY_TOLERANCE,
) -> list[OverBilledLine]:
    """Every approved quantity that exceeds what its order line has left.

    Returns all of them rather than the first: a reviewer correcting a
    three-line invoice should see three numbers, not be sent round three times.
    """
    by_id = {int(line["id"]): line for line in lines}
    over: list[OverBilledLine] = []
    for po_line_id, requested in approved.items():
        line = by_id.get(po_line_id)
        if line is None:
            # Not on this order at all. A different refusal, raised by the
            # caller's ownership check before this one runs.
            continue
        left = remaining_to_bill(line)
        if requested - left > tolerance:
            over.append(
                OverBilledLine(
                    po_line_id=po_line_id,
                    label=(
                        _relation_name(line.get("product_id"))
                        or str(line.get("name") or f"line {po_line_id}")
                    ),
                    requested=requested,
                    remaining=left,
                )
            )
    return over


def choose_receipt_picking(
    moves: list[dict[str, Any]], approved_line_ids: set[int], incoming: set[int]
) -> int:
    """The one open receipt that covers every approved line.

    Oldest first, because that is the order a vendor delivers in: the original
    receipt before its backorder.

    Refuses rather than splitting an invoice across two pickings. Deciding
    which half of a paper invoice belongs to which receipt is a judgement, and
    this code does not have the information to make it.
    """
    by_picking: dict[int, set[int]] = {}
    for move in moves:
        picking_id = _relation_id(move.get("picking_id"))
        line_id = _relation_id(move.get("purchase_line_id"))
        if picking_id is not None and picking_id in incoming and line_id is not None:
            by_picking.setdefault(picking_id, set()).add(line_id)

    for picking_id in sorted(by_picking):
        if approved_line_ids <= by_picking[picking_id]:
            return picking_id

    raise ReceiptNotPossibleError(
        "No single open receipt on this order covers every line being billed. "
        "Validate the receipts in Odoo first, then bill."
    )


def receipt_blockers(
    moves: list[dict[str, Any]], approved: dict[int, float]
) -> list[str]:
    """Everything that makes this receipt unsafe to record automatically.

    Collected rather than raised one at a time: a reviewer facing three
    problems should be told all three, not made to retry twice.
    """
    problems: list[str] = []
    by_line = {_relation_id(m.get("purchase_line_id")): m for m in moves}

    for po_line_id, quantity in approved.items():
        move = by_line.get(po_line_id)
        if move is None:
            problems.append(f"Line {po_line_id} has no open receipt to record.")
            continue

        label = _relation_name(move.get("product_id")) or f"line {po_line_id}"
        # A lot/serial number is something this system cannot invent, and
        # `button_validate` would raise deep inside the wizard — AFTER the
        # quantities were written.
        if move.get("has_tracking") not in (False, None, "none"):
            problems.append(f"{label} is lot/serial tracked; receive it in Odoo.")

        demand = float(move.get("product_uom_qty") or 0.0)
        if quantity - demand > QTY_TOLERANCE:
            problems.append(
                f"{label}: the invoice says {quantity:g}, the open receipt "
                f"expects {demand:g}."
            )

    return problems


def picking_move_writes(
    moves: list[dict[str, Any]], approved: dict[int, float], dialect: StockQtyDialect
) -> dict[int, dict[str, Any]]:
    """What to write on each move of the chosen picking.

    The zeroes are as important as the approved figures. Odoo 17 pre-fills
    `quantity` from the reservation, so a move this code does not touch is a
    move that gets received in full — the silent version of the exact failure
    this whole feature exists to prevent.
    """
    writes: dict[int, dict[str, Any]] = {}
    for move in moves:
        quantity = approved.get(_relation_id(move.get("purchase_line_id")) or -1, 0.0)
        values: dict[str, Any] = {dialect.qty_field: quantity}
        if dialect.writes_picked:
            values["picked"] = quantity > 0.0
        writes[int(move["id"])] = values
    return writes


def quantity_drift(
    read_back: list[dict[str, Any]],
    intended: dict[int, dict[str, Any]],
    *,
    qty_field: str,
    tolerance: float = QTY_TOLERANCE,
) -> list[str]:
    """Where Odoo's stored quantities disagree with what was just written.

    The cheapest possible insurance. Writing a quantity to a `stock.move` costs
    nothing and can be rewritten; validating with the WRONG quantity ships the
    goods. A UoM surprise, a record rule, or a dialect mismatch all show up
    here — while nothing has been received and the flow can still refuse.
    """
    drift: list[str] = []
    for row in read_back:
        move_id = int(row["id"])
        want = float(intended.get(move_id, {}).get(qty_field, 0.0))
        got = float(row.get(qty_field) or 0.0)
        if abs(got - want) > tolerance:
            drift.append(f"move {move_id}: wrote {want:g}, Odoo holds {got:g}")
    return drift


def classify_validate_action(action: Any) -> str:
    """What `button_validate` actually did: done, backorder, or immediate.

    It answers `True` on a clean validation — but ALSO an ordinary action dict
    when the reception report or label printing is enabled, so "a dict came
    back" does not mean "it stopped". Only the wizard models mean that.
    """
    if not isinstance(action, dict):
        return "done"
    model = action.get("res_model")
    if model == "stock.backorder.confirmation":
        return "backorder"
    if model == "stock.immediate.transfer":
        return "immediate"
    return "done"


def bill_line_edits(
    move_lines: list[dict[str, Any]],
    approved: dict[int, float],
    *,
    tolerance: float = QTY_TOLERANCE,
) -> list[tuple[Any, ...]]:
    """x2many commands reducing a full bill to the approved quantities.

    Pure, so the arithmetic that decides how much a vendor gets paid can be
    tested against a table of numbers rather than against an Odoo.

    `(2, id, 0)` deletes rather than zeroes. A bill line for 0 units posts a
    0.00 row, and Odoo's `qty_invoiced` would then count a line that says
    nothing was billed — true, but noise on an accounting document.
    """
    edits: list[tuple[Any, ...]] = []
    for row in move_lines:
        po_line_id = _relation_id(row.get("purchase_line_id"))
        if po_line_id is None:
            # A section, a note, or a line Odoo added itself. Not ours to trim.
            continue
        want = approved.get(po_line_id, 0.0)
        have = float(row.get("quantity") or 0.0)
        if want <= tolerance:
            edits.append((2, int(row["id"]), 0))
        elif abs(have - want) > tolerance:
            edits.append((1, int(row["id"]), {"quantity": want}))
    return edits


def bill_display_name(name: str | None, ref: str | None, move_id: int) -> str:
    """What to show a reviewer for a bill Odoo has not numbered yet.

    A draft vendor bill's `name` is "/" — Odoo assigns BILL/2026/08/0001 from
    the journal sequence at post time, and these bills are deliberately left in
    draft. Echoing `name` the way `OdooCreatedOrder` echoes a PO's would put a
    bare solidus on the review screen.

    The vendor's own invoice number is what the reviewer is holding in their
    hand, so that is the label; the id is what they can paste into an Odoo URL.
    """
    if name and name not in ("/", "False"):
        return name
    return f"{ref} (draft #{move_id})" if ref else f"Draft bill #{move_id}"


def group_writes(
    writes: dict[int, dict[str, Any]],
) -> list[tuple[list[int], dict[str, Any]]]:
    """Collapse per-record values into one call per distinct set of values.

    Forty moves on a picking are almost always two groups — the approved
    quantity and zero — so this turns forty round trips into two.
    """
    grouped: dict[str, tuple[list[int], dict[str, Any]]] = {}
    for record_id, values in sorted(writes.items()):
        key = json.dumps(values, sort_keys=True, default=str)
        grouped.setdefault(key, ([], values))[0].append(record_id)
    return list(grouped.values())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _load_fixture() -> list[OdooPurchaseOrder]:
    """Read purchase orders from a JSON file instead of Odoo.

    The development path, used while real credentials are unavailable. It goes
    through the same `OdooPurchaseOrder` model as the live client, so anything
    downstream — scoring, the prompt, the review screen — is exercised exactly
    as it will be in production.

    Loud on purpose. Fake purchase orders reaching an accounts-payable screen
    without anybody realising is a far worse failure than no purchase orders,
    so every fetch says so in the log and /odoo/connection reports it too.
    """
    # Belt and braces. `_enforce_production_safety` already refuses to boot a
    # production process that could reach here, so this can only fire if some
    # future code path calls the fixture loader directly — and the cost of
    # being wrong about that is a bill raised against an invented order.
    if settings.ENVIRONMENT == "production":
        raise RuntimeError(
            "CRITICAL: purchase-order fixtures are disabled in production."
        )

    path = Path(settings.ODOO_FIXTURE_PATH)
    if not path.is_absolute():
        # Relative to the server package root, so the same .env value works
        # from any working directory.
        path = Path(__file__).resolve().parent.parent.parent / path

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OdooNotConfiguredError(
            f"ODOO_FIXTURE_PATH points at {path}, which does not exist."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OdooError(f"The purchase-order fixture at {path} is not valid JSON.") from exc

    logger.warning(
        "Serving %d purchase order(s) from the FIXTURE at %s — not from Odoo",
        len(rows),
        path.name,
    )
    return [OdooPurchaseOrder.model_validate(row) for row in rows]


class OdooService:
    """Async wrapper over the blocking client."""

    async def check_connection(self) -> dict[str, Any]:
        """Authenticate and report the server version. For /health and setup.

        The cheapest call that proves the URL, database, username and key are
        all correct — worth having as its own endpoint, because diagnosing four
        possible wrong values through a failing PO fetch is miserable.
        """
        if settings.uses_odoo_fixture:
            orders = _load_fixture()
            return {
                "connected": True,
                "source": "fixture",
                "fixture_path": settings.ODOO_FIXTURE_PATH,
                "purchase_orders": len(orders),
                "warning": (
                    "Serving purchase orders from a local file. Set ODOO_URL, "
                    "ODOO_DB and ODOO_USERNAME to use the real Odoo."
                ),
            }

        client = _get_client()
        uid = await _run(client.authenticate, force=True)
        version = await _run(lambda: client._common.version())
        return {
            "connected": True,
            "uid": uid,
            "server_version": version.get("server_version"),
            "database": settings.ODOO_DB,
            "url": settings.odoo_base_url,
        }

    async def fetch_open_purchase_orders(
        self, *, limit: int | None = None
    ) -> list[OdooPurchaseOrder]:
        """Purchase orders still awaiting a vendor bill.

        The domain is configurable because "open" is deployment-specific: some
        companies invoice off `done` orders, others never leave `purchase`.
        """
        if settings.uses_odoo_fixture:
            return _load_fixture()[: limit or settings.ODOO_PO_FETCH_LIMIT]

        key = ("open", limit or settings.ODOO_PO_FETCH_LIMIT)
        hit = _cached(key)
        if hit is not None:
            return hit

        client = _get_client()
        domain: list[Any] = [
            ("state", "in", list(settings.ODOO_PO_STATES)),
            ("invoice_status", "in", list(settings.ODOO_PO_INVOICE_STATUSES)),
        ]

        rows: list[dict[str, Any]] = await _run(
            client.execute,
            "purchase.order",
            "search_read",
            domain,
            fields=PO_FIELDS,
            limit=limit or settings.ODOO_PO_FETCH_LIMIT,
            order="date_order desc",
        )

        orders = [OdooPurchaseOrder.from_odoo(row) for row in rows]
        await self._attach_lines(client, orders, rows)

        logger.info(
            "Fetched %d open purchase order(s) with %d line(s) from Odoo",
            len(orders),
            sum(len(o.lines) for o in orders),
        )
        _remember(key, orders)
        return orders

    async def fetch_recently_billed_orders(
        self, *, since: dt.date, limit: int | None = None
    ) -> list[OdooPurchaseOrder]:
        """Orders Odoo no longer considers billable, back to `since`.

        The complement of `fetch_open_purchase_orders`: same states, but every
        invoice_status the open fetch excludes.

        This exists because excluding them outright made the system lie. A
        vendor billing for an order Odoo has already invoiced — a late bill, a
        re-send, a genuine duplicate — produced an empty candidate list and the
        message "no purchase order scored highly enough", when the order was
        sitting in Odoo scoring in the nineties. Nobody can act on a match that
        was filtered out before it was scored.

        Bounded by a date window rather than fetched wholesale: closed orders
        outnumber open ones by orders of magnitude, and an invoice arriving
        years after its order is not the case worth paying for on every match.
        """
        if settings.uses_odoo_fixture:
            # The fixture is the open set by definition — it has no history.
            return []

        key = ("billed", since, limit or settings.ODOO_PO_FETCH_LIMIT)
        hit = _cached(key)
        if hit is not None:
            return hit

        client = _get_client()
        domain: list[Any] = [
            ("state", "in", list(settings.ODOO_PO_STATES)),
            ("invoice_status", "not in", list(settings.ODOO_PO_INVOICE_STATUSES)),
            ("date_order", ">=", since.isoformat()),
        ]

        rows: list[dict[str, Any]] = await _run(
            client.execute,
            "purchase.order",
            "search_read",
            domain,
            fields=PO_FIELDS,
            limit=limit or settings.ODOO_PO_FETCH_LIMIT,
            order="date_order desc",
        )

        orders = [OdooPurchaseOrder.from_odoo(row) for row in rows]
        await self._attach_lines(client, orders, rows)

        logger.info(
            "Fetched %d already-billed purchase order(s) dated on or after %s",
            len(orders),
            since.isoformat(),
        )
        _remember(key, orders)
        return orders

    async def _attach_lines(
        self,
        client: _BlockingOdooClient,
        orders: list[OdooPurchaseOrder],
        rows: list[dict[str, Any]],
    ) -> None:
        """Read every order's lines in ONE call.

        This is the whole reason this method exists. Odoo hands back
        `order_line` as a list of ids; reading them per order means one request
        per order, and 200 open orders becomes 201 network round trips against
        a server that is usually not local. Collecting the ids first turns that
        into two.
        """
        line_ids: list[int] = []
        for row in rows:
            ids = row.get("order_line") or []
            if isinstance(ids, list):
                line_ids.extend(int(i) for i in ids)

        if not line_ids:
            return

        line_rows: list[dict[str, Any]] = await _run(
            client.execute,
            "purchase.order.line",
            "read",
            line_ids,
            fields=PO_LINE_FIELDS,
        )

        by_order: dict[int, list[OdooPurchaseOrderLine]] = {}
        for line_row in line_rows:
            line = OdooPurchaseOrderLine.from_odoo(line_row)
            if line.order_id is not None:
                by_order.setdefault(line.order_id, []).append(line)

        for order in orders:
            order.lines = by_order.get(order.id, [])

    async def fetch_purchase_order(self, po_id: int) -> OdooPurchaseOrder | None:
        """One order by id, with its lines. Used when confirming a match."""
        if settings.uses_odoo_fixture:
            return next((o for o in _load_fixture() if o.id == po_id), None)

        client = _get_client()
        rows: list[dict[str, Any]] = await _run(
            client.execute, "purchase.order", "read", [po_id], fields=PO_FIELDS
        )
        if not rows:
            return None

        order = OdooPurchaseOrder.from_odoo(rows[0])
        await self._attach_lines(client, [order], rows)
        return order

    # ------------------------------------------------------------- resolution
    async def search_by_tokens(
        self, model: str, tokens: list[str], *, limit: int = 40
    ) -> list[dict[str, Any]]:
        """Records whose name contains ANY of these tokens.

        Tokens rather than the whole string, because the whole string never
        matches: Odoo holds "A J K Restaurants Management Llc" and "Eggplant
        باذنجان" while the document says "AJK Restaurants" and "Egg Plant
        (C. Int.)". An `ilike` on either of those returns nothing at all —
        measured, on this data — whereas `restaurants` and `plant` find them.
        Ranking the results is somebody else's job; this only casts the net.
        """
        if not tokens:
            return []

        # Odoo's domains are prefix notation: N terms need N-1 leading '|'.
        domain: list[Any] = ["|"] * (len(tokens) - 1)
        domain += [("name", "ilike", token) for token in tokens]

        ids: list[int] = await _run(
            _get_client().execute, model, "search", domain, limit=limit
        )
        if not ids:
            return []
        rows: list[dict[str, Any]] = await _run(
            _get_client().execute, model, "read", ids, fields=["display_name"]
        )
        return rows

    async def read_names(self, model: str, ids: list[int]) -> dict[int, str]:
        """Current display names for ids, for re-checking a stale preview."""
        if not ids:
            return {}
        rows: list[dict[str, Any]] = await _run(
            _get_client().execute, model, "read", ids, fields=["display_name"]
        )
        return {int(r["id"]): str(r["display_name"]) for r in rows}

    # -------------------------------------------------------------- creation
    async def _recent_identical_draft(
        self, client: _BlockingOdooClient, partner_id: int, expected_untaxed: float
    ) -> OdooCreatedOrder | None:
        """A draft for this vendor and this value, raised moments ago.

        Deliberately narrow — same vendor, same untaxed total, inside a short
        window — so a vendor genuinely ordered twice in a day is unaffected.
        Two identical orders minutes apart are far more likely to be one order
        clicked twice.
        """
        window = settings.ODOO_PO_DUPLICATE_WINDOW_MINUTES
        if window <= 0:
            return None

        # Odoo stores create_date in UTC.
        since = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=window)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows: list[dict[str, Any]] = await _run(
            client.execute,
            "purchase.order",
            "search_read",
            [
                ("partner_id", "=", partner_id),
                ("state", "=", "draft"),
                ("create_date", ">=", since),
            ],
            fields=["name", "amount_untaxed"],
            limit=20,
            order="id desc",
        )

        match = match_recent_draft(rows, expected_untaxed)
        if match is None:
            return None
        return OdooCreatedOrder(id=int(match["id"]), name=str(match["name"]))

    async def create_draft_purchase_order(
        self,
        *,
        partner_id: int,
        date_order: str,
        order_lines: list[dict[str, Any]],
        attachment: OdooAttachment | None = None,
    ) -> OdooCreatedOrder:
        """Create an RFQ and read back its number.

        The only write this system makes to Odoo. Three things make that safe
        to do from an automated pipeline:

          * It is created in `draft` — an RFQ, not a confirmed order. Nothing
            is ordered and nothing is owed until a person confirms it in Odoo.
            `state` is deliberately not passed: draft is the default, and
            sending it would be a claim rather than a default.
          * `taxes_id` is not passed either. Odoo applies whatever tax is
            configured against each product, which is where that decision
            belongs — an OCR'd tax figure must not overwrite an ERP's tax
            configuration.
          * Only `partner_id`, `date_order` and the lines are sent. Odoo fills
            the rest itself (name from its sequence, currency from the partner,
            company and picking type from defaults), and computing them here
            would be guessing at another system's rules.

        The scan is attached to the order when one is given. Without it the
        person confirming the RFQ in Odoo has nothing but figures to confirm
        against, and — the part that bites — a bill raised from the order
        inside Odoo inherits no document either, so somebody uploads the same
        PDF by hand. Attaching cannot fail the creation: the order exists by
        then, and `attachment_status` reports what happened.
        """
        if settings.uses_odoo_fixture:
            raise OdooNotConfiguredError(
                "Purchase orders cannot be created while running from the "
                "fixture. Set ODOO_URL, ODOO_DB and ODOO_USERNAME."
            )

        client = _get_client()

        # Before creating, check nothing identical was just created. A create
        # that reached Odoo and then failed on the way back leaves this side
        # knowing nothing, and the reviewer's natural response — click again —
        # would raise a second order for the same money.
        expected = round(
            sum(
                float(line.get("product_qty") or 0.0) * float(line.get("price_unit") or 0.0)
                for line in order_lines
            ),
            2,
        )
        existing = await self._recent_identical_draft(client, partner_id, expected)
        if existing is not None:
            logger.warning(
                "Not creating: %s (%s) is an identical draft for partner %s raised "
                "in the last %d minute(s) — returning it instead",
                existing.name,
                existing.id,
                partner_id,
                settings.ODOO_PO_DUPLICATE_WINDOW_MINUTES,
            )
            # Still attached. This branch is reached when a create reached Odoo
            # and failed on the way back, which is exactly the case where the
            # attachment never ran — and `_attach` will not duplicate one that
            # did. Returning the order without its document here would leave a
            # bare order precisely on the retry meant to heal things.
            if attachment is not None:
                status, attachment_id = await self._attach(
                    client,
                    res_model="purchase.order",
                    res_id=existing.id,
                    attachment=attachment,
                )
                return existing.model_copy(
                    update={
                        "attachment_status": status,
                        "attachment_id": attachment_id,
                    }
                )
            return existing

        values: dict[str, Any] = {
            "partner_id": partner_id,
            "date_order": date_order,
            # (0, 0, {...}) is Odoo's "create a new child record" command.
            "order_line": [(0, 0, line) for line in order_lines],
        }

        # `create` given a LIST of vals is a multi-create and answers with a
        # list of ids — one entry here, but a list all the same. Odoo also
        # accepts a bare dict and answers with a bare id, and which form comes
        # back has varied across versions, so both are handled: getting this
        # wrong once already left an order in Odoo that this side never saw.
        result = await _run(client.execute, "purchase.order", "create", [values])
        po_id = int(result[0] if isinstance(result, list) else result)

        rows: list[dict[str, Any]] = await _run(
            client.execute, "purchase.order", "read", [po_id], fields=["name"]
        )
        name = str(rows[0]["name"]) if rows else f"PO-{po_id}"

        status, attachment_id = "none", None
        if attachment is not None:
            status, attachment_id = await self._attach(
                client, res_model="purchase.order", res_id=po_id, attachment=attachment
            )

        logger.info(
            "Created draft purchase order %s (%s) for partner %s with %d line(s), "
            "attachment=%s",
            name,
            po_id,
            partner_id,
            len(order_lines),
            status,
        )
        # A new order changes what "open" means, and a cached fetch would keep
        # answering with the set from before it existed.
        clear_fetch_cache()
        return OdooCreatedOrder(
            id=po_id, name=name, attachment_status=status, attachment_id=attachment_id
        )

    # --------------------------------------------------------------- billing
    async def _capabilities(self, model: str) -> frozenset[str]:
        """Which of the probed fields this Odoo's `model` actually has."""
        with _caps_lock:
            hit = _caps.get(model)
        if hit is not None:
            return hit

        rows: dict[str, Any] = await _run(
            _get_client().execute, model, "fields_get", _PROBES[model],
            attributes=["type"],
        )
        present = frozenset(rows or {})
        with _caps_lock:
            _caps[model] = present
        logger.info(
            "Odoo %s speaks: %s", model, sorted(present) or "(none of the probed fields)"
        )
        return present

    async def find_vendor_bills(
        self, *, partner_id: int, ref: str
    ) -> list[OdooExistingBill]:
        """Bills Odoo already holds for this vendor reference.

        The duplicate guard, and the first Odoo call the billing flow makes —
        before the order is read, long before anything is written.

        `child_of` rather than `=`: Odoo's own `_prepare_invoice` files a bill
        against the vendor's INVOICE address, which is frequently a child
        contact of the partner on the order, so an exact match would miss the
        duplicate this exists to catch. On a partner with no children `child_of`
        degrades to `=`.

        Cancelled bills are excluded. A cancelled document is Odoo's record that
        the bill was a mistake, and treating it as a duplicate would permanently
        block the correct one from ever being raised.
        """
        if not ref:
            return []

        client = _get_client()
        fields = list(BILL_FIELDS)
        # Renamed from `invoice_payment_state` in Odoo 14. Asking for a field
        # this database does not have is a fault, not an empty column.
        if "payment_state" in await self._capabilities("account.move"):
            fields.append("payment_state")

        rows: list[dict[str, Any]] = await _run(
            client.execute,
            "account.move",
            "search_read",
            [
                ("move_type", "=", "in_invoice"),
                ("partner_id", "child_of", partner_id),
                ("ref", "=", ref),
                ("state", "in", ["draft", "posted"]),
            ],
            fields=fields,
            limit=5,
            order="id desc",
        )
        return [OdooExistingBill.from_odoo(row) for row in rows]

    async def receive_purchase_order_lines(
        self, *, po_id: int, quantities: dict[int, float]
    ) -> OdooReceiptResult:
        """Validate an open receipt for exactly these quantities.

        Odoo keeps the remainder as a backorder, which is what makes a 100-piece
        order deliverable and billable in two halves.

        Separate from `create_vendor_bill` on purpose, and not for tidiness.
        `button_validate` is the one call in this feature that cannot be undone
        with an `unlink` — once stock moves are `done`, reversing them needs a
        return picking and a person. If billing then fails, the retry must be
        able to skip straight to the bill, and it can only do that if receiving
        was never welded to it.

        The order of operations is the design: everything that can refuse does
        so before the first write, the quantities are written and then READ BACK
        and compared, and only then is the picking validated. Writing a quantity
        costs nothing and can be rewritten; validating the wrong one ships the
        goods.
        """
        if settings.uses_odoo_fixture:
            raise OdooNotConfiguredError(
                "Goods receipts cannot be recorded while running from the "
                "fixture. Set ODOO_URL, ODOO_DB and ODOO_USERNAME."
            )
        if not quantities:
            raise ReceiptNotPossibleError("No quantities were given to receive.")

        client = _get_client()
        dialect = stock_qty_dialect(await self._capabilities("stock.move"))

        # --- reads and pure refusals, nothing written yet --------------------
        moves: list[dict[str, Any]] = await _run(
            client.execute,
            "stock.move",
            "search_read",
            [
                ("purchase_line_id", "in", list(quantities)),
                ("state", "not in", ["done", "cancel"]),
            ],
            fields=[*STOCK_MOVE_FIELDS, dialect.qty_field],
            limit=500,
            order="id asc",
        )
        if not moves:
            raise ReceiptNotPossibleError(
                "This order has no open goods receipt in Odoo. Either it has "
                "already been received in full, or the receipt was cancelled.",
                code="NO_OPEN_RECEIPT",
            )

        candidate_ids = sorted(
            {pid for m in moves if (pid := _relation_id(m.get("picking_id"))) is not None}
        )
        pickings: list[dict[str, Any]] = await _run(
            client.execute,
            "stock.picking",
            "read",
            candidate_ids,
            fields=["name", "state", "picking_type_code", "backorder_id"],
        )
        # A 2- or 3-step warehouse chains internal transfers off the same PO
        # lines. Validating one of those is not receiving from the vendor.
        incoming = {
            int(p["id"]) for p in pickings if p.get("picking_type_code") == "incoming"
        }

        picking_id = choose_receipt_picking(moves, set(quantities), incoming)
        on_picking = [
            m for m in moves if _relation_id(m.get("picking_id")) == picking_id
        ]

        problems = receipt_blockers(on_picking, quantities)
        if problems:
            raise ReceiptNotPossibleError(
                "This receipt cannot be recorded automatically: "
                + "; ".join(problems)
                + ". Nothing has been received.",
                details={"problems": problems},
            )

        # --- reversible write ------------------------------------------------
        writes = picking_move_writes(on_picking, quantities, dialect)
        for move_ids, values in group_writes(writes):
            await _run(client.execute, "stock.move", "write", move_ids, values)

        # --- read back, and refuse while refusing is still free --------------
        check_fields = [dialect.qty_field] + (
            ["picked"] if dialect.writes_picked else []
        )
        read_back: list[dict[str, Any]] = await _run(
            client.execute, "stock.move", "read", list(writes), fields=check_fields
        )
        drift = quantity_drift(read_back, writes, qty_field=dialect.qty_field)
        if drift:
            raise ReceiptNotPossibleError(
                "Odoo did not store the receipt quantities as written "
                f"({'; '.join(drift)}). Nothing has been received.",
                code="RECEIPT_QTY_REJECTED",
                details={"drift": drift},
            )

        # --- the irreversible call -------------------------------------------
        context: dict[str, Any] = {
            "active_model": "stock.picking",
            "active_id": picking_id,
            "active_ids": [picking_id],
            # Deliberately NOT set: `skip_immediate` and `skip_backorder`. They
            # look like conveniences; they are the difference between detecting
            # that the partial quantities were ignored and silently receiving
            # the entire demand. See `classify_validate_action`.
        }
        action = await _run(
            client.execute,
            "stock.picking",
            "button_validate",
            [picking_id],
            context=context,
        )
        kind = classify_validate_action(action)

        if kind == "immediate":
            raise ReceiptNotPossibleError(
                "Odoo asked to process the whole demand, which means the "
                "partial quantities were not registered. Nothing has been "
                "received.",
                code="RECEIPT_QTY_REJECTED",
            )
        if kind == "backorder":
            # The action's own context carries the wizard's defaults; merging it
            # is what keeps this working across versions that disagree about
            # which of them the wizard needs.
            wizard_context = {**context, **(action.get("context") or {})}
            created = await _run(
                client.execute,
                "stock.backorder.confirmation",
                "create",
                # Passed explicitly as well as inherited from the context: a
                # wizard created with no pick_ids validates nothing and reports
                # success.
                [{"pick_ids": [(6, 0, [picking_id])]}],
                context=wizard_context,
            )
            wizard_id = int(created[0] if isinstance(created, list) else created)
            await _run(
                client.execute,
                "stock.backorder.confirmation",
                # `process` keeps the remainder as a backorder — the 50 the
                # vendor still owes. `process_cancel_backorder` would close the
                # order at 50 of 100, which is a different business decision and
                # not this one.
                "process",
                [wizard_id],
                context=wizard_context,
            )

        # `button_validate` can return an action after a SUCCESSFUL validation
        # (the reception report) and a wizard after an unsuccessful one, so its
        # return value alone cannot say which happened. The picking can.
        after: list[dict[str, Any]] = await _run(
            client.execute,
            "stock.picking",
            "read",
            [picking_id],
            fields=["name", "state", "backorder_ids"],
        )
        if not after or after[0].get("state") != "done":
            raise OdooError("The goods receipt did not complete in Odoo.")

        backorder_ids = [int(i) for i in (after[0].get("backorder_ids") or [])]
        backorder_names = await self.read_names("stock.picking", backorder_ids)

        # `qty_received` and `invoice_status` both just moved, and both are
        # filtered on by `fetch_open_purchase_orders`. Cleared here rather than
        # only at the end of billing, because billing can fail in between.
        clear_fetch_cache()
        logger.info(
            "Received %s (%s) on order %s: %s, backorder(s) %s",
            after[0].get("name"),
            picking_id,
            po_id,
            {k: round(v, 3) for k, v in quantities.items()},
            list(backorder_names.values()) or "none",
        )
        return OdooReceiptResult(
            picking_id=picking_id,
            picking_name=str(after[0].get("name") or picking_id),
            backorder_ids=backorder_ids,
            backorder_names=list(backorder_names.values()),
            received=dict(quantities),
        )

    async def _po_invoice_ids(self, client: _BlockingOdooClient, po_id: int) -> set[int]:
        rows: list[dict[str, Any]] = await _run(
            client.execute, "purchase.order", "read", [po_id], fields=["invoice_ids"]
        )
        return {int(i) for i in (rows[0].get("invoice_ids") or [])} if rows else set()

    async def create_vendor_bill(
        self,
        *,
        po_id: int,
        quantities: dict[int, float],
        vendor_ref: str | None,
        invoice_date: str | None = None,
        attachment: OdooAttachment | None = None,
    ) -> OdooCreatedBill:
        """Create a DRAFT vendor bill for exactly these quantities.

        Odoo's own `action_create_invoice` does the creating, rather than this
        code building an `account.move` directly. `purchase.order._prepare_
        invoice` sets the journal, the fiscal position (including the fallback
        when the order has none), payment terms, partner bank, the vendor's
        invoice address, currency, and per-line taxes and expense accounts — and
        it is heavily inherited by localisation modules. Reimplementing that
        here would mean shipping a copy of an ERP's accounting rules that drifts
        on every upgrade and silently loses whatever the deployment actually
        does. In accounts payable that is the wrong thing to own.

        Its one weakness is quantity: it bills `qty_to_invoice`, which under a
        product's "on ordered quantities" policy is the whole order. So the
        lines are trimmed afterwards, and both bill-control policies converge on
        the same result. Under Odoo's default the trim is a no-op, because the
        receipt already limited what there was to bill.

        The bill is left in DRAFT. Nothing is owed until a person confirms it in
        Odoo — the same rule `create_draft_purchase_order` follows, and it
        applies with more force to an accounts-payable document. It also keeps
        Odoo's own duplicate-reference warning useful: on Odoo 16 that check
        raises on post, where it would arrive here as an opaque 502.
        """
        if settings.uses_odoo_fixture:
            raise OdooNotConfiguredError(
                "Vendor bills cannot be created while running from the fixture. "
                "Set ODOO_URL, ODOO_DB and ODOO_USERNAME."
            )

        client = _get_client()

        before = await self._po_invoice_ids(client, po_id)
        await _run(client.execute, "purchase.order", "action_create_invoice", [po_id])
        after = await self._po_invoice_ids(client, po_id)
        new_ids = sorted(after - before)

        # The action dict `action_create_invoice` returns carries `res_id` for a
        # single move and a `domain` for several, and which one appears has
        # varied across versions. Diffing the order's own `invoice_ids` is the
        # answer that does not depend on that.
        if not new_ids:
            raise NothingToBillError(
                "Odoo created no bill for this order — it has nothing left to "
                "invoice. Either the goods are not receipted yet, or the order "
                "is already fully billed."
            )
        if len(new_ids) > 1:
            raise OdooError("Odoo created more than one bill for this order.")
        bill_id = new_ids[0]

        # --- trim to the approved quantities, and stamp the reference --------
        move_lines: list[dict[str, Any]] = await _run(
            client.execute,
            "account.move.line",
            "search_read",
            [("move_id", "=", bill_id), ("purchase_line_id", "!=", False)],
            fields=["purchase_line_id", "quantity", "product_id", "name"],
        )

        values: dict[str, Any] = {}
        if vendor_ref:
            # `_prepare_invoice` sets `ref` from the ORDER's `partner_ref`, not
            # from the vendor's invoice number. Left alone, the duplicate guard
            # would search on the wrong string for every later invoice.
            values["ref"] = vendor_ref
            values["payment_reference"] = vendor_ref
        if invoice_date:
            values["invoice_date"] = invoice_date

        edits = bill_line_edits(move_lines, quantities)
        if edits:
            values["invoice_line_ids"] = edits

        if values:
            # One write, not three. Each one re-runs the move's tax and total
            # computations, and a bill that briefly holds the full order at the
            # right reference is a worse intermediate state than one that never
            # exists.
            await _run(client.execute, "account.move", "write", [bill_id], values)

        rows: list[dict[str, Any]] = await _run(
            client.execute,
            "account.move",
            "read",
            [bill_id],
            fields=["name", "ref", "state", "amount_untaxed", "amount_total", "currency_id"],
        )
        row = rows[0] if rows else {}
        name = str(row.get("name") or "/")
        ref = row.get("ref") or vendor_ref

        status, attachment_id = "none", None
        if attachment is not None:
            status, attachment_id = await self._attach(
                client, res_model="account.move", res_id=bill_id, attachment=attachment
            )

        clear_fetch_cache()
        logger.info(
            "Created draft vendor bill %s (ref=%r) on order %s with %d line(s), "
            "attachment=%s",
            bill_id,
            ref,
            po_id,
            len(quantities),
            status,
        )
        return OdooCreatedBill(
            id=bill_id,
            name=name,
            ref=ref,
            display_name=bill_display_name(name, ref, bill_id),
            state=str(row.get("state") or "draft"),
            amount_untaxed=float(row.get("amount_untaxed") or 0.0),
            amount_total=float(row.get("amount_total") or 0.0),
            currency=_relation_name(row.get("currency_id")),
            attachment_status=status,
            attachment_id=attachment_id,
        )

    async def attach_document(
        self, *, res_model: str, res_id: int, attachment: OdooAttachment
    ) -> tuple[str, int | None]:
        """Put the scanned document on an Odoo record. Never raises.

        Public, and taking the model, because the same document belongs in two
        places. A reviewer confirming a purchase order in Odoo needs the paper
        it was raised from just as much as one posting the payable does — and a
        bill Odoo generates from that order inherits nothing, so attaching only
        at bill time leaves every order bare and every bill made from one in
        Odoo bare with it.
        """
        return await self._attach(
            _get_client(), res_model=res_model, res_id=res_id, attachment=attachment
        )

    async def _attach(
        self,
        client: _BlockingOdooClient,
        *,
        res_model: str,
        res_id: int,
        attachment: OdooAttachment,
    ) -> tuple[str, int | None]:
        """The write itself. Never raises; reports what happened instead.

        By the time this runs the record exists and cannot be un-created from
        here, so a failure must not become the request's failure — a bill
        missing its PDF is fixed by a person in ten seconds, whereas answering
        502 for a request that succeeded leaves the reviewer clicking again
        into our own duplicate guard.

        A plain `ir.attachment`, deliberately NOT `message_post`. That sets
        `message_main_attachment_id`, which on Odoo with `account_invoice_
        extract` installed triggers AI digitisation — and it would happily
        overwrite the vendor, date, lines and totals a bill has just had
        trimmed. Checked against this deployment: an attachment written this
        way comes out field-for-field identical to one a person uploads through
        the chatter, and Odoo lists it in the same place.

        Idempotent by name, because the callers are: the bill flow attaches to
        an order that may already carry the scan, and a reviewer who clicks
        twice must not file the same document against the same record twice.
        """
        cap = settings.ODOO_ATTACHMENT_MAX_MB * 1024 * 1024
        if len(attachment.content) > cap:
            logger.warning(
                "%s %s: not attaching %s (%.1f MB, over the %d MB limit)",
                res_model,
                res_id,
                attachment.file_name,
                len(attachment.content) / 1_048_576,
                settings.ODOO_ATTACHMENT_MAX_MB,
            )
            return "skipped", None

        try:
            already: list[int] = await _run(
                client.execute,
                "ir.attachment",
                "search",
                [
                    ("res_model", "=", res_model),
                    ("res_id", "=", res_id),
                    ("name", "=", attachment.file_name),
                ],
                limit=1,
            )
            if already:
                logger.info(
                    "%s %s already carries %s — not attaching it twice",
                    res_model,
                    res_id,
                    attachment.file_name,
                )
                return "attached", int(already[0])

            result = await _run(
                client.execute,
                "ir.attachment",
                "create",
                [
                    {
                        # The extension is kept: Odoo's preview keys off it.
                        "name": attachment.file_name,
                        "type": "binary",
                        # A base64 `str`, never `xmlrpc.client.Binary`. Binary
                        # marshals as <base64>, which Odoo's XML-RPC layer
                        # decodes on the way in — handing the field raw bytes it
                        # then tries to base64-decode a second time.
                        "datas": base64.b64encode(attachment.content).decode("ascii"),
                        "res_model": res_model,
                        "res_id": res_id,
                        "mimetype": attachment.mime_type or "application/octet-stream",
                    }
                ],
            )
        except Exception:
            logger.exception(
                "%s %s exists but the document could not be attached",
                res_model,
                res_id,
            )
            return "failed", None

        return "attached", int(result[0] if isinstance(result, list) else result)


odoo_service = OdooService()
