"""Typed application settings, loaded from .env.

Never call os.getenv() elsewhere — import `settings` from here so every config
value has a declared type and a single definition.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    PROJECT_NAME: str = "OCR API"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = True

    # SecretStr so an accidental print(settings) or a logged traceback cannot
    # leak the database password.
    DATABASE_URL: SecretStr

    # NoDecode stops pydantic-settings from json.loads()-ing the raw env string
    # before the validator runs, which is what lets `A,B` work as well as JSON.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # ---------------------------------------------------------------- JWT
    # Generated per-process if unset so development works out of the box. A
    # random default is a deliberate choice over a fixed placeholder: it makes
    # tokens stop working across restarts, which is annoying enough to notice
    # in development and impossible to accidentally ship as a real secret.
    # The model_validator below hard-fails if it is missing in production.
    JWT_SECRET_KEY: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(64))
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ---------------------------------------------------------------- cookies
    AUTH_COOKIE_NAME: str = "refresh_token"
    # Must be True in production — a refresh token sent over plain HTTP is
    # readable by anyone on the network path.
    AUTH_COOKIE_SECURE: bool = False
    AUTH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    # Scoping the cookie to the auth routes means it is not attached to every
    # ordinary API call, which shrinks its exposure considerably.
    AUTH_COOKIE_PATH: str = "/api/v1/auth"
    AUTH_COOKIE_DOMAIN: str | None = None

    # ---------------------------------------------------------------- storage
    # Cloudflare R2 (S3-compatible object storage).
    #
    # All optional at boot, on purpose. The service must still start for an
    # engineer working on auth who has no R2 account; the storage layer raises
    # StorageNotConfiguredError (503) on first use instead. See the note in
    # `_enforce_production_safety` about promoting this to a boot-time failure.
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: SecretStr = SecretStr("")
    R2_SECRET_ACCESS_KEY: SecretStr = SecretStr("")
    R2_BUCKET_NAME: str = "ap-invoices"

    # Custom domain or r2.dev URL for public reads. Leave blank for a private
    # bucket — objects are then reachable only through a presigned URL.
    R2_PUBLIC_URL: str = ""

    UPLOAD_MAX_SIZE_MB: int = Field(default=10, ge=1, le=200)

    @field_validator("R2_PUBLIC_URL", "R2_ACCOUNT_ID", "R2_BUCKET_NAME", mode="after")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        # A trailing slash pasted from the Cloudflare dashboard would otherwise
        # produce "https://files.example.com//invoices/...".
        return v.strip().rstrip("/")

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):  # JSON array form
                import json

                return list(json.loads(v))
            return [o.strip().rstrip("/") for o in v.split(",") if o.strip()]
        return [str(origin) for origin in v]

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        """Fail fast on insecure production configuration.

        Catching this at startup is the difference between a deploy that
        refuses to boot and one that silently serves refresh tokens over
        cleartext for a month.
        """
        if self.ENVIRONMENT != "production":
            return self

        problems: list[str] = []
        if not self.model_fields_set.intersection({"JWT_SECRET_KEY"}):
            problems.append("JWT_SECRET_KEY must be set explicitly in production")
        if len(self.JWT_SECRET_KEY.get_secret_value()) < 32:
            problems.append("JWT_SECRET_KEY must be at least 32 characters")
        if not self.AUTH_COOKIE_SECURE:
            problems.append("AUTH_COOKIE_SECURE must be true in production")
        if self.AUTH_COOKIE_SAMESITE == "none" and not self.AUTH_COOKIE_SECURE:
            problems.append("SameSite=none requires Secure cookies")
        if self.DEBUG:
            problems.append("DEBUG must be false in production")

        if problems:
            raise ValueError(
                "Unsafe production configuration:\n  - " + "\n  - ".join(problems)
            )
        return self

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

    # ------------------------------------------------------------- storage
    @property
    def r2_endpoint_url(self) -> str:
        """R2's S3 API endpoint. Note this is NOT the public read URL — it is
        the authenticated control endpoint boto3 signs requests against."""
        return f"https://{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    @property
    def is_storage_configured(self) -> bool:
        return bool(
            self.R2_ACCOUNT_ID
            and self.R2_ACCESS_KEY_ID.get_secret_value()
            and self.R2_SECRET_ACCESS_KEY.get_secret_value()
            and self.R2_BUCKET_NAME
        )

    @property
    def upload_max_size_bytes(self) -> int:
        return self.UPLOAD_MAX_SIZE_MB * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
