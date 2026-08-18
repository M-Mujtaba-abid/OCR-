"""What the client is allowed to do, as the server understands it.

This exists to kill a duplication. The upload screen has to know the size cap,
the file count and the accepted types in order to reject a file before sending
it — and it used to know them by holding its own copy, with a comment saying
"mirrors the backend". Two copies of a limit is one limit and one bug waiting:
raise it in the server's environment and the browser goes on refusing the file;
raise it in the browser and the server refuses it after the upload.

So the numbers are defined once, in the server's settings, and served from
here. Changing `UPLOAD_MAX_SIZE_MB` in one environment now changes what the
browser enforces, with no redeploy of the frontend.

Unauthenticated on purpose: it discloses nothing but limits — the same limits
any caller learns by being refused — and requiring a session would mean the
upload screen could not validate until the session query resolved.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core import storage
from app.core.config import settings
from app.lib.responses import ApiResponse
from app.schemas.config import PublicConfig

router = APIRouter(tags=["config"])


@router.get(
    "/config",
    response_model=ApiResponse[PublicConfig],
    summary="Limits the client should enforce before calling the API",
)
async def public_config() -> ApiResponse[PublicConfig]:
    return ApiResponse.ok(
        PublicConfig(
            max_file_bytes=settings.upload_max_size_bytes,
            max_files_per_upload=settings.MAX_FILES_PER_UPLOAD,
            accepted_mime_types=sorted(storage.ALLOWED_MIME_TYPES),
        )
    )
