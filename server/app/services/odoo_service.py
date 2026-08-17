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

import datetime as dt
import functools
import json
import threading
import time
import xmlrpc.client
from pathlib import Path
from typing import Any

import anyio.to_thread

from app.core.config import settings
from app.core.exceptions import OdooAuthError, OdooError, OdooNotConfiguredError
from app.lib.logging import get_logger
from app.schemas.odoo import OdooPurchaseOrder, OdooPurchaseOrderLine

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
]

PO_LINE_FIELDS = [
    "order_id",
    "name",
    "product_id",
    "product_qty",
    "qty_invoiced",
    "price_unit",
    "price_subtotal",
    "price_tax",
    "price_total",
    "product_uom",
]


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
    """Drop the cached client and its uid. For tests and config reloads."""
    global _client
    with _client_lock:
        _client = None


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Offload a blocking call, translating faults into typed errors."""
    try:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    except (OdooAuthError, OdooNotConfiguredError):
        raise
    except xmlrpc.client.Fault as fault:
        if _is_session_fault(fault):
            raise OdooAuthError() from fault
        # A fault message can carry the database name and internal model paths.
        # Log it, never return it.
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


odoo_service = OdooService()
