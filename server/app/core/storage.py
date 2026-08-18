"""Cloudflare R2 object storage.

R2 speaks the S3 API, so boto3 is the client — with three R2-specific settings
that are easy to get wrong and fail confusingly:

  1. ``region_name="auto"``. R2 has no regions, but SigV4 requires *a* region in
     the signature. Omit it and botocore raises NoRegionError before any request
     leaves the process.
  2. ``signature_version="s3v4"``. R2 rejects the older v2 signatures.
  3. Checksums set to ``when_required``. botocore >= 1.36 started sending a
     CRC32 checksum header on *every* PutObject. S3-compatible providers that do
     not implement it reject the request outright, and the error message points
     at the checksum rather than at the client default that caused it.

Everything here is I/O against a third party, so every public function is async
and every boto3 call is offloaded to a worker thread — boto3 is entirely
synchronous and would otherwise block the event loop for the whole upload.

This module is deliberately free of database and HTTP concerns: it takes an
UploadFile and returns a value object. Persisting the result is the service
layer's job.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
import threading
import unicodedata
import uuid
from pathlib import PurePosixPath
from typing import Any, Final

import anyio.to_thread
import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    StorageError,
    StorageNotConfiguredError,
    UnsupportedFileTypeError,
)
from app.lib.logging import get_logger

# boto3 builds its clients at runtime from JSON service models, so there is no
# real class to annotate against without adding the boto3-stubs package. Aliased
# here so the intent is readable and the day stubs are added it is a one-line
# change.
S3Client = Any

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Accepted content
# ---------------------------------------------------------------------------
# Magic-byte signatures, checked against the first bytes of the upload.
#
# The client's Content-Type header is NOT trusted for this: it is attacker
# controlled, and a browser will happily label anything "application/pdf".
# Sniffing is what actually keeps a script out of the bucket.
#
# python-magic is avoided on purpose — it needs a libmagic DLL that does not
# ship on Windows. Four formats do not justify that dependency.
_MAGIC_SIGNATURES: Final[tuple[tuple[bytes, str], ...]] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II\x2a\x00", "image/tiff"),  # little-endian (Intel)
    (b"MM\x00\x2a", "image/tiff"),  # big-endian (Motorola)
)

ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/tiff"}
)

_EXTENSION_BY_MIME: Final[dict[str, str]] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
}

# Longest signature above; only this many bytes are needed to identify a file.
_SNIFF_BYTES: Final[int] = 8

# Read granularity. Small enough that an oversized upload is rejected after
# ~1 MiB rather than after the client has finished sending 500 MiB.
_CHUNK_SIZE: Final[int] = 1024 * 1024

# SigV4 caps presigned URL lifetime at 7 days.
_MAX_PRESIGN_SECONDS: Final[int] = 7 * 24 * 3600


class StorageResult(BaseModel):
    """What was actually stored.

    ``key`` is the durable identifier — persist it. ``url`` is a convenience
    for rendering and is only meaningful when the bucket is public; for a
    private bucket, call :func:`generate_presigned_url` at read time instead of
    storing an expiring URL in the database.
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(description="Object key within the bucket.")
    url: str = Field(description="Public URL, or the endpoint URL if private.")
    size_bytes: int
    mime_type: str
    original_name: str = Field(description="Sanitised filename, safe to echo.")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
_client: S3Client | None = None
_client_lock = threading.Lock()


def get_r2_client() -> S3Client:
    """Return the process-wide R2 client, creating it on first use.

    boto3 clients are safe to *use* from multiple threads but not safe to
    *create* concurrently, which is exactly what would happen the first time two
    uploads land together. Hence the lock rather than an ``lru_cache``.
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:  # another thread won the race
            return _client

        if not settings.is_storage_configured:
            raise StorageNotConfiguredError()

        _client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID.get_secret_value(),
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY.get_secret_value(),
            config=BotoConfig(
                region_name="auto",
                signature_version="s3v4",
                # Path-style keeps the bucket in the path rather than in a
                # subdomain. R2 supports both, but path-style avoids a DNS
                # dependency on bucket naming and makes presigned URLs readable.
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        )
        logger.info(
            "R2 client initialised (bucket=%s, endpoint=%s)",
            settings.R2_BUCKET_NAME,
            settings.r2_endpoint_url,
        )
        return _client


def reset_r2_client() -> None:
    """Drop the cached client. For tests and for config reloads only."""
    global _client
    with _client_lock:
        _client = None


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_COLLAPSE_UNDERSCORES = re.compile(r"_{2,}")
_MAX_STEM_LENGTH: Final[int] = 80


def sanitize_filename(raw: str | None, *, fallback_mime: str | None = None) -> str:
    """Reduce a client-supplied filename to something safe to put in a key.

    Three separate problems are being solved:

      * **Path traversal.** ``../../etc/passwd`` and ``C:\\Windows\\x.pdf`` are
        reduced to their basename, so a filename can never escape its prefix.
      * **Non-ASCII.** Object metadata travels in HTTP headers, which are
        latin-1 at best; ``Rechnung_Müller.pdf`` becomes ``Rechnung_Muller.pdf``
        rather than raising deep inside botocore.
      * **Length.** Long names plus a UUID prefix push against key limits and,
        on Windows, MAX_PATH once anything is downloaded.
    """
    name = PurePosixPath((raw or "").replace("\\", "/")).name
    # NFKD then ASCII-drop turns accented characters into their base letters
    # instead of deleting the whole word.
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")

    suffix = PurePosixPath(name).suffix
    stem = name[: len(name) - len(suffix)] if suffix else name

    stem = _COLLAPSE_UNDERSCORES.sub("_", _UNSAFE_CHARS.sub("_", stem)).strip("._-")
    suffix = _UNSAFE_CHARS.sub("", suffix).lower()

    if not stem:
        stem = "upload"
    stem = stem[:_MAX_STEM_LENGTH]

    if not suffix and fallback_mime:
        suffix = _EXTENSION_BY_MIME.get(fallback_mime, "")

    return f"{stem}{suffix}"


def build_object_key(
    folder: str,
    filename: str,
    *,
    tenant_id: str = "default",
    now: dt.datetime | None = None,
) -> str:
    """``{folder}/{tenant_id}/{YYYY-MM}/{uuid}_{filename}``.

    Tenant first inside the folder so a whole tenant can be listed, exported or
    deleted with a single prefix operation. The month partition keeps any one
    prefix from growing without bound, which matters for listing performance and
    for lifecycle rules. The UUID guarantees two members uploading
    ``invoice.pdf`` in the same month cannot overwrite each other — object
    storage has no "file already exists" error, a PUT just silently replaces.
    """
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y-%m")
    safe_tenant = _UNSAFE_CHARS.sub("_", tenant_id) or "default"
    return f"{folder.strip('/')}/{safe_tenant}/{stamp}/{uuid.uuid4().hex}_{filename}"


def sniff_mime_type(head: bytes) -> str | None:
    """Identify a file from its leading bytes, or None if unrecognised."""
    for signature, mime in _MAGIC_SIGNATURES:
        if head.startswith(signature):
            return mime
    return None


def public_url(key: str) -> str:
    """The URL to store alongside the key.

    Falls back to the authenticated endpoint when no public domain is set. That
    URL is not readable anonymously — it exists so the column is never null and
    the object is still identifiable from a database row alone.
    """
    if settings.R2_PUBLIC_URL:
        return f"{settings.R2_PUBLIC_URL}/{key}"
    return f"{settings.r2_endpoint_url}/{settings.R2_BUCKET_NAME}/{key}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
async def _read_and_validate(file: UploadFile) -> tuple[bytes, str]:
    """Buffer the upload, enforcing size and type. Returns (bytes, mime_type).

    Size is checked *while* reading rather than from ``file.size``, because that
    attribute reflects the Content-Length the client declared and a client that
    lies about it is precisely the case worth defending against.

    Files up to UPLOAD_MAX_SIZE_MB are held in memory. At the default 10 MB
    that is fine; if that limit is ever raised past ~50 MB, switch this to
    streaming ``upload_fileobj`` against the SpooledTemporaryFile instead.
    """
    limit = settings.upload_max_size_bytes

    await file.seek(0)
    buffer = bytearray()
    while chunk := await file.read(_CHUNK_SIZE):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise FileTooLargeError(
                f"File exceeds the {settings.UPLOAD_MAX_SIZE_MB} MB limit."
            )

    if not buffer:
        raise EmptyFileError()

    mime_type = sniff_mime_type(bytes(buffer[:_SNIFF_BYTES]))
    if mime_type is None or mime_type not in ALLOWED_MIME_TYPES:
        raise UnsupportedFileTypeError()

    return bytes(buffer), mime_type


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def _put_object_sync(
    *, key: str, body: bytes, mime_type: str, original_name: str
) -> None:
    """Blocking PutObject. Always called through a worker thread."""
    client = get_r2_client()
    client.put_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=key,
        Body=body,
        ContentType=mime_type,
        # So a browser download restores the user's filename rather than the
        # UUID-prefixed key. Already ASCII-only via sanitize_filename.
        ContentDisposition=f'inline; filename="{original_name}"',
        Metadata={"original-filename": original_name},
    )


async def upload_file(
    file: UploadFile,
    folder: str,
    *,
    tenant_id: str = "default",
) -> StorageResult:
    """Validate and store an upload. Raises, never returns a partial result.

    Failure modes are separated on purpose so the caller can react correctly:
    a 4xx from here means the client sent something unusable and retrying is
    pointless, while :class:`StorageError` means R2 misbehaved and a retry may
    well work.
    """
    body, mime_type = await _read_and_validate(file)
    original_name = sanitize_filename(file.filename, fallback_mime=mime_type)
    key = build_object_key(folder, original_name, tenant_id=tenant_id)

    try:
        await anyio.to_thread.run_sync(
            functools.partial(
                _put_object_sync,
                key=key,
                body=body,
                mime_type=mime_type,
                original_name=original_name,
            )
        )
    except (ClientError, BotoCoreError) as exc:
        # The key is logged, the credentials and the exception's own message are
        # not echoed to the client — a botocore error can contain the endpoint
        # and bucket, which is not the caller's business.
        logger.exception("R2 upload failed for key=%s", key)
        raise StorageError() from exc

    logger.info("Stored %s (%d bytes, %s)", key, len(body), mime_type)
    return StorageResult(
        key=key,
        url=public_url(key),
        size_bytes=len(body),
        mime_type=mime_type,
        original_name=original_name,
    )


async def delete_file(key: str) -> bool:
    """Delete an object. Returns success rather than raising.

    Deletion is used for best-effort cleanup after a failed database write, and
    in that path an exception would mask the original error. S3 DeleteObject is
    idempotent — deleting an absent key succeeds — so a False return means the
    call genuinely failed, not that the object was already gone.
    """
    try:
        await anyio.to_thread.run_sync(
            functools.partial(
                get_r2_client().delete_object,
                Bucket=settings.R2_BUCKET_NAME,
                Key=key,
            )
        )
    except (ClientError, BotoCoreError, StorageNotConfiguredError):
        logger.exception("R2 delete failed for key=%s", key)
        return False

    logger.info("Deleted %s", key)
    return True


async def generate_presigned_url(key: str, expires: int = 3600) -> str:
    """A time-limited authenticated download URL.

    This is how a private bucket is read. The signature is computed locally, so
    no network call is made and the URL is valid immediately.

    Do not persist the result: it expires, and a URL in a database column
    outlives its own signature.
    """
    ttl = max(1, min(expires, _MAX_PRESIGN_SECONDS))
    try:
        url: str = await anyio.to_thread.run_sync(
            functools.partial(
                get_r2_client().generate_presigned_url,
                "get_object",
                Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
                ExpiresIn=ttl,
            )
        )
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Presign failed for key=%s", key)
        raise StorageError() from exc
    return url


async def download_file(key: str) -> bytes:
    """Read a whole object into memory.

    The counterpart to :func:`generate_presigned_url`, for the one case a signed
    URL cannot serve: handing the bytes to a third party that will not fetch a
    URL. Odoo's `ir.attachment` takes base64 content, not a link — and a link
    would be the wrong answer anyway, because the attachment has to outlive the
    signature by years.

    The size is established with a HeadObject *before* the body is fetched. A
    `get_object` on an oversized key would buffer all of it into a worker's
    memory before anything here could object, and on a serverless worker with a
    fixed ceiling that is an OOM rather than an error message.

    The caller holds the whole result in memory, so this is deliberately not the
    function to reach for once the upload limit moves past ~50 MB — stream to a
    SpooledTemporaryFile then, exactly as the upload path already notes.
    """

    def _size() -> int:
        response = get_r2_client().head_object(
            Bucket=settings.R2_BUCKET_NAME, Key=key
        )
        return int(response["ContentLength"])

    def _body() -> bytes:
        response = get_r2_client().get_object(
            Bucket=settings.R2_BUCKET_NAME, Key=key
        )
        return bytes(response["Body"].read())

    try:
        size = await anyio.to_thread.run_sync(_size)
    except ClientError as exc:
        # A key with no object behind it. Distinct from a transport failure:
        # retrying will never produce the file, so the caller should not.
        logger.info("Download refused: no object at key=%s", key)
        raise StorageError("That file is no longer in storage.") from exc
    except BotoCoreError as exc:
        logger.exception("HeadObject failed for key=%s", key)
        raise StorageError() from exc

    if size > settings.upload_max_size_bytes:
        raise FileTooLargeError(
            f"That file is {size / 1_048_576:.1f} MB, past the "
            f"{settings.UPLOAD_MAX_SIZE_MB} MB limit."
        )

    try:
        body = await anyio.to_thread.run_sync(_body)
    except (ClientError, BotoCoreError) as exc:
        # The key is logged; the botocore message is not echoed — it can carry
        # the endpoint and the bucket, which are not the caller's business.
        logger.exception("R2 download failed for key=%s", key)
        raise StorageError() from exc

    logger.info("Read %s (%d bytes)", key, len(body))
    return body


async def generate_upload_url(
    key: str, *, mime_type: str, original_name: str, expires: int | None = None
) -> str:
    """A time-limited URL the BROWSER can PUT one object to.

    This exists because a serverless request body is capped at 4.5 MB, which a
    scanned invoice routinely exceeds. Sending the bytes straight from the
    browser to R2 takes the platform out of the path entirely — the API only
    ever handles the key.

    `ContentType` and `ContentDisposition` are signed INTO the URL, so the
    client cannot store the object as some other type than the one the server
    approved: a request whose headers do not match the signature is refused by
    R2, not by us.
    """
    ttl = max(
        1, min(expires or settings.UPLOAD_SIGNED_URL_TTL, _MAX_PRESIGN_SECONDS)
    )
    try:
        url: str = await anyio.to_thread.run_sync(
            functools.partial(
                get_r2_client().generate_presigned_url,
                "put_object",
                Params={
                    "Bucket": settings.R2_BUCKET_NAME,
                    "Key": key,
                    "ContentType": mime_type,
                    "ContentDisposition": f'inline; filename="{original_name}"',
                },
                ExpiresIn=ttl,
            )
        )
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Upload presign failed for key=%s", key)
        raise StorageError() from exc
    return url


class StoredObject(BaseModel):
    """What R2 says is actually there, after a direct upload."""

    model_config = ConfigDict(frozen=True)

    key: str
    size_bytes: int
    #: Sniffed from the object's own leading bytes, never from what the client
    #: declared. This is the check the old server-side upload did in
    #: `_read_and_validate`, moved to the far side of the transfer.
    mime_type: str


async def inspect_uploaded_object(key: str) -> StoredObject:
    """Confirm an object exists and is what it claims to be.

    The trust boundary for direct upload. Between handing out a signed URL and
    being told "it's there", the only things known for certain are what R2 will
    tell us — so both the size and the type are re-established here rather than
    taken from the client:

      * HeadObject for the true byte count, checked against the same limit the
        old path enforced while reading.
      * A ranged GET of the first bytes for the magic number, so a `.txt`
        renamed to `.pdf` is refused exactly as it was before.

    Raises the same typed errors the old path raised, so callers already
    written to catch them keep working.
    """

    def _head() -> int:
        response = get_r2_client().head_object(
            Bucket=settings.R2_BUCKET_NAME, Key=key
        )
        return int(response["ContentLength"])

    def _head_bytes() -> bytes:
        response = get_r2_client().get_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
            Range=f"bytes=0-{_SNIFF_BYTES - 1}",
        )
        return bytes(response["Body"].read())

    try:
        size = await anyio.to_thread.run_sync(_head)
    except ClientError as exc:
        # A key that was never written, or was written to a prefix this caller
        # does not own — either way there is nothing to register.
        logger.info("Register refused: no object at key=%s", key)
        raise EmptyFileError("That upload did not complete.") from exc
    except BotoCoreError as exc:
        logger.exception("HeadObject failed for key=%s", key)
        raise StorageError() from exc

    if size == 0:
        raise EmptyFileError()
    if size > settings.upload_max_size_bytes:
        raise FileTooLargeError(
            f"File exceeds the {settings.UPLOAD_MAX_SIZE_MB} MB limit."
        )

    try:
        head = await anyio.to_thread.run_sync(_head_bytes)
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Range read failed for key=%s", key)
        raise StorageError() from exc

    mime_type = sniff_mime_type(head)
    if mime_type is None or mime_type not in ALLOWED_MIME_TYPES:
        raise UnsupportedFileTypeError()

    return StoredObject(key=key, size_bytes=size, mime_type=mime_type)


async def check_storage() -> bool:
    """Cheap connectivity probe for /health/ready.

    HeadBucket is the lightest call that still proves the credentials are valid
    and the bucket exists — as opposed to a ListObjects, which can be expensive
    and only proves read access.
    """
    try:
        await anyio.to_thread.run_sync(
            functools.partial(
                get_r2_client().head_bucket, Bucket=settings.R2_BUCKET_NAME
            )
        )
    except Exception:
        logger.warning("R2 health check failed for bucket=%s", settings.R2_BUCKET_NAME)
        return False
    return True
