"""Logging setup and redaction helpers.

Reusable infrastructure only — no business logic, per the `lib/` rule.
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar

# Bound by the request-context middleware; read by the formatter so every line
# emitted during a request carries its id without being passed a logger.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Patterns whose values must never reach a log file. The security rules forbid
# logging passwords, access tokens and refresh tokens; relying on developers to
# remember that is how they end up in logs anyway, so scrub centrally.
_REDACT_PATTERNS = [
    re.compile(r'("password"\s*:\s*")[^"]*(")', re.I),
    re.compile(r'("password_hash"\s*:\s*")[^"]*(")', re.I),
    re.compile(r'("refresh_token"\s*:\s*")[^"]*(")', re.I),
    re.compile(r'("access_token"\s*:\s*")[^"]*(")', re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I),
    re.compile(r"(refresh_token=)[^;\s]+", re.I),
]


def redact(text: str) -> str:
    """Replace secret values with ***. Applied to every formatted log record."""
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub(
            lambda m: (m.group(1) + "***" + (m.group(2) if m.lastindex and m.lastindex > 1 else "")),
            text,
        )
    return text


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO", *, debug_sql: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # SQL echo is deafening once real traffic starts; opt in explicitly.
    logging.getLogger("sqlalchemy.engine.Engine").setLevel(
        logging.INFO if debug_sql else logging.WARNING
    )
    for noisy in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
