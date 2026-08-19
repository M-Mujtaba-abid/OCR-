"""Turning a company's name into an object-storage-safe handle.

A company's slug ends up inside every one of its object keys
(`invoices/{slug}/2026-08/...`), which makes it a path segment rather than a
label. That is why this is stricter than a display name needs to be: anything
that could introduce a slash, traverse a prefix or collide with another
company's is removed here, before the value is ever stored.
"""

from __future__ import annotations

import re
import unicodedata

#: Everything that is not a-z, 0-9 or a hyphen.
_UNSAFE = re.compile(r"[^a-z0-9]+")
_COLLAPSE = re.compile(r"-{2,}")

MAX_SLUG_LENGTH = 48

#: Words that would make a URL or a bucket prefix ambiguous.
_RESERVED = frozenset(
    {"", "admin", "api", "platform", "static", "public", "health", "www"}
)


def slugify(name: str) -> str:
    """A storage-safe handle for this company name.

    NFKD then ASCII-drop, so "Café Noir" becomes "cafe-noir" rather than losing
    the whole accented word — the same normalisation `sanitize_filename` uses,
    for the same reason.

    Returns "" when nothing usable survives; the caller decides what to do
    about that rather than having a placeholder invented for them.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    slug = _COLLAPSE.sub("-", _UNSAFE.sub("-", ascii_name.lower())).strip("-")
    return slug[:MAX_SLUG_LENGTH].strip("-")


def is_reserved(slug: str) -> bool:
    """Whether this slug would be ambiguous as a path segment."""
    return slug in _RESERVED


def unique_slug(name: str, taken: set[str]) -> str:
    """A slug for `name` that is not already in `taken`.

    Collisions get a numeric suffix rather than a random one: two companies
    genuinely called "Bright Foods" become `bright-foods` and `bright-foods-2`,
    which stays legible in a bucket listing. A random suffix would be unique
    and unreadable, and these appear in storage paths people have to grep.
    """
    base = slugify(name)
    if not base or is_reserved(base):
        base = f"company-{base}" if base else "company"

    if base not in taken:
        return base

    for suffix in range(2, 1000):
        candidate = f"{base[: MAX_SLUG_LENGTH - len(str(suffix)) - 1]}-{suffix}"
        if candidate not in taken:
            return candidate

    raise ValueError(f"Could not derive a unique slug from {name!r}.")
