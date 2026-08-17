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

    # How long after a token is rotated that re-presenting it is treated as a
    # duplicate rather than as theft.
    #
    # Without this, any client that fires two refreshes at once — a retry, a
    # double-click, two tabs waking together — has every one of its sessions
    # revoked. The second request arrives moments after the first rotated the
    # token, which is indistinguishable from a replay if you only look at the
    # revoked flag.
    #
    # The window costs little: a replay inside it still gets a 401 and still
    # receives no session, because the successor was already issued to someone
    # else. All it forgoes is the revoke-everything response, for a few
    # seconds. Signing an honest user out of every device for double-clicking
    # is the worse failure.
    REFRESH_REUSE_GRACE_SECONDS: int = Field(default=10, ge=0, le=120)

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

    # Per-request file count. Bounded because each file is buffered in memory
    # during validation, so N x UPLOAD_MAX_SIZE_MB is the real memory cost of
    # one request — 10 x 10 MB is a deliberate ceiling, not an arbitrary one.
    MAX_FILES_PER_UPLOAD: int = Field(default=10, ge=1, le=50)

    # ---------------------------------------------------------------- OCR / AI
    MISTRAL_API_KEY: SecretStr = SecretStr("")
    MISTRAL_OCR_MODEL: str = "mistral-ocr-latest"
    MISTRAL_CHAT_MODEL: str = "mistral-large-latest"
    # The reranker's model, separately settable.
    #
    # It shared MISTRAL_CHAT_MODEL with the extraction fallback, which meant a
    # cheaper model could not be tried on the one job suited to it — picking
    # between a handful of scored candidates — without also degrading the job
    # that reads a document from scratch. Empty means "same as the chat model",
    # so nothing changes until somebody deliberately measures an alternative.
    MISTRAL_RERANK_MODEL: str = ""

    # The kill switch. Every upload costs money once OCR runs automatically;
    # flipping this to false reverts to admin-triggered processing without a
    # code change or a redeploy.
    OCR_AUTO_ON_UPLOAD: bool = True

    # Mistral caps document annotation at 8 pages (plain OCR allows 1000).
    # Past this the service falls back to OCR-then-chat over the markdown.
    OCR_MAX_ANNOTATION_PAGES: int = Field(default=8, ge=1, le=8)

    # How long the signed URL handed to Mistral stays valid. Long enough for a
    # large PDF to be fetched, short enough that a leaked log line is useless.
    OCR_SIGNED_URL_TTL: int = Field(default=600, ge=60, le=3600)

    # ---------------------------------------------------------------- Odoo
    ODOO_URL: str = ""
    ODOO_DB: str = ""
    ODOO_USERNAME: str = ""
    ODOO_API_KEY: SecretStr = SecretStr("")

    # Which purchase orders are eligible for matching. Kept as config because
    # every deployment's definition of "still open" differs — some invoice off
    # 'done' orders, some never leave 'purchase'.
    ODOO_PO_STATES: Annotated[list[str], NoDecode] = ["purchase", "done"]
    #: Which orders are still billable. A list, not one value: a deployment may
    #: legitimately consider both "to invoice" and "no" open for matching.
    ODOO_PO_INVOICE_STATUSES: Annotated[list[str], NoDecode] = ["to invoice"]
    # A ceiling on one fetch. Matching narrows candidates locally anyway, and an
    # unbounded search_read against a large Odoo is how a request times out.
    ODOO_PO_FETCH_LIMIT: int = Field(default=500, ge=1, le=5000)
    # How long a fetch's result is reused. A twenty-file upload otherwise runs
    # twenty identical reads of several hundred orders and a thousand lines,
    # serially, against the same Odoo. Nobody bills for that, but the queue
    # waits for it. Short enough that an order created moments ago is not one
    # anybody is billing yet; 0 disables the cache.
    ODOO_FETCH_CACHE_SECONDS: int = Field(default=60, ge=0, le=3600)

    # A JSON file of purchase orders to use INSTEAD of a live Odoo.
    #
    # Only honoured when ODOO_URL is empty, so it can never shadow a real
    # connection: filling in the real credentials disables it automatically and
    # nothing else changes. Every fetch logs a warning and /odoo/connection
    # reports source="fixture", because silently serving fake purchase orders
    # to an accounts-payable screen would be worse than serving none.
    ODOO_FIXTURE_PATH: str = ""

    # ---------------------------------------------------------------- matching
    # How many candidates reach the LLM. This is the number that keeps the
    # design affordable: the model sees 15 orders, never 5000.
    MATCH_CANDIDATE_LIMIT: int = Field(default=15, ge=1, le=50)
    # Below this a candidate is not worth showing the LLM at all.
    MATCH_SCORE_FLOOR: float = Field(default=35.0, ge=0, le=100)
    # Below this the verdict is recorded as no_match rather than a weak guess.
    MATCH_MIN_CONFIDENCE: float = Field(default=70.0, ge=0, le=100)
    # How far back to also consider orders Odoo has ALREADY billed.
    #
    # Without this the correct order simply vanishes whenever Odoo has marked
    # it invoiced, and the screen reports "no match" — indistinguishable from
    # the order not existing. Vendors bill late and bill twice, so an
    # already-invoiced order matching an incoming invoice is not noise: it is
    # the duplicate-billing case, and it has to be visible to be caught.
    #
    # A window rather than the whole history: an Odoo with tens of thousands of
    # closed orders would otherwise be pulled into every match for nothing.
    # Set to 0 to switch the sweep off entirely.
    MATCH_CLOSED_LOOKBACK_DAYS: int = Field(default=90, ge=0, le=3650)

    # ------------------------------------------------------- prompt economy
    # What the model is actually shown. The shortlist above is what the REVIEW
    # SCREEN keeps — every candidate considered, which is what makes a wrong
    # match arguable afterwards. These decide which of them are worth paying
    # tokens to describe, and the two are not the same question.
    #
    # A candidate 25 points below the leader is not what the model picks; it is
    # only billed for. Where the top of the list is genuinely tied nothing is
    # trimmed, so the spend follows the difficulty of the decision.
    MATCH_PROMPT_MARGIN: float = Field(default=25.0, ge=0, le=100)
    # Never describe fewer than this, however far ahead the leader is. A
    # shortlist of one is a decision already made, and the model cannot
    # disagree with a choice it was not offered.
    MATCH_PROMPT_MIN: int = Field(default=5, ge=1, le=50)
    # Line rows per candidate in the prompt. The line-item SCORE is computed in
    # code over every line and travels in the breakdown; these rows exist so the
    # model can judge whether the goods are the same, which twelve answer as
    # well as twenty-five.
    MATCH_PROMPT_ITEM_CAP: int = Field(default=12, ge=1, le=100)

    # When the prefilter's answer is beyond argument, the model is not asked.
    #
    # Deliberately strict: it requires the vendor to have quoted the order's
    # reference EXACTLY — the one signal that states the answer outright — plus
    # a score this high and a gap this wide. Set the score above 100 to switch
    # the fast path off entirely.
    MATCH_AUTO_ACCEPT_SCORE: float = Field(default=95.0, ge=0, le=101)
    MATCH_AUTO_ACCEPT_MARGIN: float = Field(default=20.0, ge=0, le=100)

    @field_validator("R2_PUBLIC_URL", "R2_ACCOUNT_ID", "R2_BUCKET_NAME", mode="after")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        # A trailing slash pasted from the Cloudflare dashboard would otherwise
        # produce "https://files.example.com//invoices/...".
        return v.strip().rstrip("/")

    @field_validator("ODOO_PO_STATES", "ODOO_PO_INVOICE_STATUSES", mode="before")
    @classmethod
    def _parse_po_states(cls, v: Any) -> list[str]:
        # Same comma-or-JSON handling as CORS_ORIGINS, for the same reason:
        # a .env file cannot hold a Python list.
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return list(json.loads(v))
            return [s.strip() for s in v.split(",") if s.strip()]
        return [str(state) for state in v]

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

    # ------------------------------------------------------------- OCR / Odoo
    @property
    def is_ocr_configured(self) -> bool:
        return bool(self.MISTRAL_API_KEY.get_secret_value())

    @property
    def is_odoo_configured(self) -> bool:
        return bool(
            self.ODOO_URL
            and self.ODOO_DB
            and self.ODOO_USERNAME
            and self.ODOO_API_KEY.get_secret_value()
        )

    @property
    def odoo_base_url(self) -> str:
        """Normalised, no trailing slash — the XML-RPC paths are appended."""
        return self.ODOO_URL.strip().rstrip("/")

    @property
    def uses_odoo_fixture(self) -> bool:
        """A real connection always wins over the fixture."""
        return bool(self.ODOO_FIXTURE_PATH) and not self.ODOO_URL.strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
