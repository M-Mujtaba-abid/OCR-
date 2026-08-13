"""Typed application settings, loaded from .env.

Never call os.getenv() elsewhere — import `settings` from here so every config
value has a declared type and a single definition.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Query params that libpq/psycopg understand but asyncpg does NOT. Neon hands
# you a libpq-flavoured URL containing these; passing them through makes
# asyncpg raise `TypeError: connect() got an unexpected keyword argument
# 'sslmode'`. We strip them and configure TLS explicitly in db/session.py.
_LIBPQ_ONLY_PARAMS = {
    "sslmode",
    "channel_binding",
    "options",
    "target_session_attrs",
    "connect_timeout",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        # utf-8-sig, not utf-8: PowerShell and Notepad write a BOM that would
        # otherwise become part of the first variable's name.
        env_file_encoding="utf-8-sig",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "AP Invoice Automation API"
    ENVIRONMENT: str = "local"
    DEBUG: bool = True

    # SecretStr so an accidental print(settings) or a logged traceback cannot
    # leak the database password.
    DATABASE_URL: SecretStr

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    @property
    def async_dsn(self) -> str:
        """The DATABASE_URL rewritten for SQLAlchemy's asyncpg driver.

        Two changes to what Neon gives you:
          1. scheme  postgresql://  ->  postgresql+asyncpg://
          2. drop the libpq-only query params listed above
        """
        parts = urlsplit(self.DATABASE_URL.get_secret_value())
        kept = [(k, v) for k, v in parse_qsl(parts.query) if k not in _LIBPQ_ONLY_PARAMS]

        if self.is_pooled:
            # SQLAlchemy's asyncpg dialect keeps its OWN prepared-statement cache
            # on top of asyncpg's, and PgBouncer breaks both. This disables the
            # dialect-level one; asyncpg's own is disabled in db/session.py.
            # It must travel as a URL param — create_async_engine() rejects it
            # as a keyword argument.
            kept.append(("prepared_statement_cache_size", "0"))

        return urlunsplit(
            ("postgresql+asyncpg", parts.netloc, parts.path, urlencode(kept), parts.fragment)
        )

    @property
    def db_host(self) -> str:
        """Host only — safe to log or return from /health, unlike the full DSN."""
        return urlsplit(self.DATABASE_URL.get_secret_value()).hostname or "unknown"

    @property
    def is_pooled(self) -> bool:
        """Neon's pooled endpoint runs PgBouncer, which breaks asyncpg's
        prepared-statement cache. Detected here so session.py can disable it."""
        return "-pooler." in self.db_host


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
