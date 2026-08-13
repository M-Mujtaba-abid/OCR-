"""Cryptographic primitives: password hashing, JWTs, refresh tokens.

This module is deliberately I/O-free and framework-free. It knows nothing about
HTTP, the database, or FastAPI, which makes every function here directly
unit-testable.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings
from app.core.exceptions import InvalidTokenError, TokenExpiredError

TokenType = Literal["access"]

# ---------------------------------------------------------------------------
# Password hashing — Argon2id via pwdlib
# ---------------------------------------------------------------------------
# Argon2id is the OWASP-recommended algorithm: memory-hard, so it resists the
# GPU/ASIC attacks that make bcrypt and PBKDF2 progressively weaker.
#
# pwdlib over passlib: passlib 1.7.4 is unmaintained and breaks on new Python
# and bcrypt releases. pwdlib is actively maintained and is what FastAPI's own
# documentation now recommends.
_password_hash = PasswordHash((Argon2Hasher(),))


def hash_password(plain_password: str) -> str:
    """Return an Argon2id hash. The salt is generated internally and embedded
    in the output string, so no separate salt column is needed."""
    return _password_hash.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Constant-time verification. Never raises on a malformed hash."""
    try:
        return _password_hash.verify(plain_password, password_hash)
    except Exception:
        # A corrupt or foreign-format hash must read as "wrong password", not
        # as a 500 that tells an attacker something about this account.
        return False


def verify_and_update_password(
    plain_password: str, password_hash: str
) -> tuple[bool, str | None]:
    """Verify, and return a rehashed value when Argon2's parameters have been
    raised since this hash was made. Call on successful login and persist the
    new hash if one comes back — that upgrades users transparently."""
    try:
        return _password_hash.verify_and_update(plain_password, password_hash)
    except Exception:
        return False, None


# A pre-computed hash used when the email does not exist. Verifying against it
# costs the same as a real check, so an attacker cannot tell registered emails
# from unregistered ones by timing the response.
DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-equalisation")


# ---------------------------------------------------------------------------
# Access tokens — short-lived JWTs
# ---------------------------------------------------------------------------
def create_access_token(
    user_id: uuid.UUID | str,
    *,
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, dt.datetime]:
    """Build a signed access JWT.

    Returns (token, expires_at) so the caller can tell the client when to
    refresh without decoding the token itself.

    Claims are kept minimal on purpose: anything embedded here is readable by
    anyone holding the token (JWTs are signed, not encrypted) and cannot be
    revoked before it expires. Roles are deliberately NOT included — they are
    read from the database on each request so a revoked role takes effect
    immediately rather than up to 15 minutes later.
    """
    now = dt.datetime.now(dt.UTC)
    expires_at = now + dt.timedelta(
        minutes=expires_minutes or settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
        **(extra_claims or {}),
    }
    token = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify signature, expiry and token type. Raises AppError subclasses."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            # Explicitly require the claims we depend on, rather than getting
            # None later and treating it as valid.
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, malformed structure, wrong algorithm, and the
        # `alg: none` attack — PyJWT rejects algorithms outside the allow-list.
        raise InvalidTokenError() from exc

    # Without this check, a token minted for another purpose would be accepted
    # as an access token.
    if payload.get("type") != "access":
        raise InvalidTokenError("Expected an access token.")
    return payload


# ---------------------------------------------------------------------------
# Refresh tokens — opaque, database-backed
# ---------------------------------------------------------------------------
REFRESH_TOKEN_BYTES = 48  # 384 bits of entropy


def generate_refresh_token() -> str:
    """A cryptographically secure random string.

    `secrets` draws from the OS CSPRNG. `random`, `uuid4` alone, and anything
    seeded from a timestamp are all unsuitable — uuid4 in particular carries
    only 122 bits and its generation is not guaranteed to be cryptographic on
    every platform.
    """
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(raw_token: str) -> str:
    """Return a deterministic SHA-256 hex digest of the refresh token.

    Why SHA-256 here and Argon2 for passwords — the difference is deliberate:

      * A password is low-entropy and human-chosen, so a stolen hash must be
        made expensive to brute-force. Argon2 does that.
      * A refresh token is 384 bits from a CSPRNG. It cannot be brute-forced at
        any cost, so a slow hash buys nothing and adds latency to every refresh.
      * Argon2 salts each hash randomly, which makes lookup-by-hash impossible.
        We must find the session by its token, so the digest has to be
        deterministic and indexable.

    Storing the digest rather than the token means a database leak does not
    hand the attacker usable sessions.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def refresh_tokens_equal(raw_token: str, stored_hash: str) -> bool:
    """Compare in constant time to avoid leaking the digest byte-by-byte."""
    return hmac.compare_digest(hash_refresh_token(raw_token), stored_hash)
