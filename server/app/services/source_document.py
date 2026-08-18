"""The scanned document behind an invoice, fetched for Odoo.

Its own module because two flows need it and neither owns it: a purchase order
raised from an invoice and a vendor bill posted against one both want the paper
attached in Odoo. It lived inside the bill creator until the order path needed
it too, and a private import across two service modules is how a shared rule
ends up with two copies that drift.
"""

from __future__ import annotations

from app.core import storage
from app.lib.logging import get_logger
from app.models.match_history import MatchHistory
from app.schemas.odoo import OdooAttachment

logger = get_logger(__name__)


async def read_source_document(invoice: MatchHistory) -> OdooAttachment | None:
    """Fetch the scanned invoice out of storage. Never fails the caller.

    A storage failure must not stop an order or a bill being created: the
    document is evidence attached to the record, and a reviewer can drag it
    onto it in Odoo in ten seconds. Refusing to bill because the PDF could not
    be read would hold up the payable for the least important part of it.

    `None` means "carry on without it", and every caller is written to.
    """
    try:
        content = await storage.download_file(invoice.file_key)
    except Exception:
        logger.exception(
            "Invoice %s: source document could not be read from storage; "
            "continuing without it",
            invoice.id,
        )
        return None

    return OdooAttachment(
        file_name=invoice.file_name,
        mime_type=invoice.mime_type or "application/pdf",
        content=content,
    )
