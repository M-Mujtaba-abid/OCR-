"""Client-facing configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PublicConfig(BaseModel):
    """The limits a browser needs to check a file before uploading it.

    Every value here is derived from the server's own settings, so this is the
    single place the numbers live. The client holds no copy.
    """

    max_file_bytes: int = Field(description="Largest single file, in bytes.")
    max_files_per_upload: int
    accepted_mime_types: list[str] = Field(
        description="Sniffed from content on arrival, not taken from the name."
    )
