"""Model registry.

Importing every model here is what populates `Base.metadata`. Alembic's
autogenerate compares that metadata against the live database, so a model not
imported here is invisible to migrations and silently never gets a table.
"""

from app.db.base import Base
from app.models.auth_session import AuthSession
from app.models.user import User, UserRole

__all__ = ["Base", "AuthSession", "User", "UserRole"]
