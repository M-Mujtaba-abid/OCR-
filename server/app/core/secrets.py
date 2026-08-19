"""Encryption for secrets the application has to be able to read back.

Passwords are hashed: they are checked, never recovered, so a one-way function
is both sufficient and safer. An Odoo API key is the opposite — it has to be
replayed verbatim to Odoo on every call — so it must be reversible, and the
question stops being "which algorithm" and becomes "where does the key live".

The key lives in the environment (`SECRETS_ENCRYPTION_KEY`) and never in the
database beside the thing it protects. That is the entire point: a leaked
database dump, a stolen backup, a mis-set permission on a read replica — none
of them hand over two companies' ERP logins on their own.

Fernet, not raw AES. It carries its own IV, authenticates the ciphertext so a
tampered value fails loudly instead of decrypting to rubbish, and stamps a
timestamp — none of which is optional in practice and all of which is easy to
get wrong by hand.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import AppError


class SecretsNotConfiguredError(AppError):
    """No encryption key, so a stored credential can neither be read nor written.

    A 503 rather than a 500: the deployment is missing configuration, which is
    a thing an operator fixes, not a bug in a request.
    """

    status_code = 503
    code = "SECRETS_NOT_CONFIGURED"
    message = (
        "Encrypted credentials are unavailable because no encryption key is "
        "configured on the server."
    )


class SecretDecryptionError(AppError):
    """The stored value did not decrypt under the current key.

    Almost always means the key was rotated or replaced without re-encrypting
    what it protected. Deliberately NOT reported as "wrong credentials": the
    credentials may be perfect and unreadable, and sending an operator to
    re-enter them would hide the real fault.
    """

    status_code = 500
    code = "SECRET_DECRYPTION_FAILED"
    message = (
        "A stored credential could not be decrypted. The encryption key may "
        "have changed since it was saved."
    )


def _cipher() -> Fernet:
    key = settings.SECRETS_ENCRYPTION_KEY.get_secret_value().strip()
    if not key:
        raise SecretsNotConfiguredError()
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        # A malformed key is a deployment fault, and saying so plainly beats a
        # traceback about base64 padding.
        raise SecretsNotConfiguredError(
            "SECRETS_ENCRYPTION_KEY is not a valid Fernet key. Generate one "
            "with: python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        ) from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential for storage. Returns ASCII-safe text."""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Read a stored credential back."""
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError() from exc


def generate_key() -> str:
    """A new Fernet key, for operators setting a deployment up."""
    return Fernet.generate_key().decode("ascii")


def is_configured() -> bool:
    """Whether secrets can be read or written at all.

    For health checks and for the settings screen, which should say "no
    encryption key configured" rather than failing when somebody tries to save
    credentials.
    """
    key = settings.SECRETS_ENCRYPTION_KEY.get_secret_value().strip()
    if not key:
        return False
    try:
        Fernet(key.encode("ascii"))
    except (ValueError, TypeError):
        return False
    return True


def looks_like_fernet(value: str) -> bool:
    """Cheap shape check, for telling an encrypted column from a plaintext one.

    Used by the credential loader to refuse a value that was written straight
    into the column by hand — better to fail than to send what somebody
    believed was ciphertext to Odoo as a password.
    """
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        return False
    # Fernet: 0x80 version byte, 8-byte timestamp, 16-byte IV, ciphertext, and
    # a 32-byte HMAC — so anything shorter cannot be one.
    return len(raw) >= 57 and raw[0] == 0x80
