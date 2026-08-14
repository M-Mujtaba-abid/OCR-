"""Model registry.

Importing every model here is what populates `Base.metadata`. Alembic's
autogenerate compares that metadata against the live database, so a model not
imported here is invisible to migrations and silently never gets a table.
"""

from app.db.base import Base
from app.models.auth_session import AuthSession
from app.models.invoice_line_match import InvoiceLineMatch, LineMatchStatus
from app.models.match_history import (
    OPEN_STATUSES,
    WITHDRAWABLE_STATUSES,
    InvoiceStatus,
    MatchHistory,
)
from app.models.notification import Notification, NotificationType
from app.models.processing_batch import BatchStatus, ProcessingBatch
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "AuthSession",
    "BatchStatus",
    "InvoiceLineMatch",
    "InvoiceStatus",
    "LineMatchStatus",
    "MatchHistory",
    "Notification",
    "NotificationType",
    "OPEN_STATUSES",
    "ProcessingBatch",
    "User",
    "UserRole",
    "WITHDRAWABLE_STATUSES",
]
