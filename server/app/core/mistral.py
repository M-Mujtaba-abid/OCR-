"""Mistral client: OCR and structured extraction.

Mirrors `core/storage.py` in shape — lazy singleton behind a lock, typed
exceptions, never logs the key — with one difference that matters: the Mistral
SDK exposes genuinely async methods (`ocr.process_async`, `chat.complete_async`),
so unlike boto3 there is nothing to offload to a worker thread.

Two operational facts drive the design here:

  * **Document annotation caps at 8 pages.** Plain OCR handles 1000 pages and
    50 MB, but the structured-extraction pass does not. Past the cap this falls
    back to OCR-then-chat over the markdown.
  * **The document is fetched by Mistral, not uploaded to it.** The PDF already
    lives in R2, and a presigned URL is enough. That avoids sending every file
    to a second vendor's storage and removes a whole round trip.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from mistralai.client import Mistral
from mistralai.client.models import DocumentURLChunk
from mistralai.extra import response_format_from_pydantic_model
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.exceptions import (
    ExtractionInvalidError,
    OcrError,
    OcrNotConfiguredError,
)
from app.lib.logging import get_logger

logger = get_logger(__name__)

#: Appended to every annotation prompt.
#:
#: The rounding rule is not cosmetic. Under a strict JSON schema with `number`
#: typed fields, the model will happily emit the full binary expansion of a
#: float — `300.0000000000000606636270...` — and exhaust its output budget
#: mid-number, truncating the JSON. Saying "two decimals" prevents the runaway;
#: `_repair_json` catches it if it happens anyway.
NUMERIC_DISCIPLINE = (
    "\n\nOUTPUT DISCIPLINE:\n"
    "- Round every monetary value to at most 2 decimal places. "
    "Write 300.00, never 300.000000000000060663627.\n"
    "- Quantities: write whole numbers where the document shows whole numbers.\n"
    "- Numbers must be JSON numbers, never strings, and must carry no currency "
    "symbol or thousands separator.\n"
    "- Return only the JSON object. No prose, no markdown fences."
)

_client: Mistral | None = None
_client_lock = threading.Lock()


def get_mistral_client() -> Mistral:
    """Return the process-wide client, creating it on first use."""
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:  # another thread won the race
            return _client
        if not settings.is_ocr_configured:
            raise OcrNotConfiguredError()

        _client = Mistral(api_key=settings.MISTRAL_API_KEY.get_secret_value())
        logger.info("Mistral client initialised (ocr=%s)", settings.MISTRAL_OCR_MODEL)
        return _client


def reset_mistral_client() -> None:
    """Drop the cached client. For tests and config reloads only."""
    global _client
    with _client_lock:
        _client = None


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
# Transport failures only. A document Mistral cannot read will fail identically
# on every attempt, and each attempt is billed — so a 4xx must not be retried.
# The SDK raises its own error types; matching on the status attribute keeps
# this working across SDK versions rather than importing a moving target.
def _is_retriable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        return True  # connection reset, timeout, DNS — worth another attempt
    return bool(status == 429 or status >= 500)


_retry = retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
class OcrOutcome(BaseModel):
    """What one OCR pass produced, before it is written anywhere."""

    model_config = {"arbitrary_types_allowed": True}

    markdown: str
    page_count: int
    #: The structured extraction, when the annotation pass ran and returned one.
    annotation: dict[str, Any] | None = None
    #: The verbatim response, kept for debugging a bad extraction later.
    raw: dict[str, Any]
    model: str


async def run_ocr(
    document_url: str,
    *,
    annotation_model: type[BaseModel] | None = None,
    annotation_prompt: str | None = None,
) -> OcrOutcome:
    """OCR a document, optionally extracting structured fields in the same call.

    `document_url` is fetched by Mistral, so it must be reachable from the
    public internet for the length of the call — a presigned R2 URL is exactly
    that, and expires shortly afterwards.

    Passing `annotation_model` asks for OCR **and** extraction in one request.
    That is both cheaper and more accurate than OCR-then-chat, because the model
    sees the page layout rather than a flattened markdown rendering of it.
    """
    client = get_mistral_client()

    kwargs: dict[str, Any] = {
        "model": settings.MISTRAL_OCR_MODEL,
        "document": DocumentURLChunk(document_url=document_url),
    }
    if annotation_model is not None:
        kwargs["document_annotation_format"] = response_format_from_pydantic_model(
            annotation_model
        )
        # The discipline block goes on unconditionally — it is what stops the
        # model from writing a number so long the response never closes.
        kwargs["document_annotation_prompt"] = (
            annotation_prompt or ""
        ) + NUMERIC_DISCIPLINE

    try:
        response = await _call_ocr(client, kwargs)
    except Exception as exc:
        # The provider's message can contain the signed document URL, which is
        # a credential for the length of its TTL. Log the type, never the text.
        logger.exception("Mistral OCR failed (%s)", type(exc).__name__)
        raise OcrError() from exc

    pages = response.pages or []
    markdown = "\n\n".join(page.markdown or "" for page in pages).strip()

    annotation: dict[str, Any] | None = None
    if response.document_annotation:
        annotation = _as_dict(response.document_annotation)

    return OcrOutcome(
        markdown=markdown,
        page_count=len(pages),
        annotation=annotation,
        raw=response.model_dump(mode="json"),
        model=response.model or settings.MISTRAL_OCR_MODEL,
    )


@_retry
async def _call_ocr(client: Mistral, kwargs: dict[str, Any]) -> Any:
    return await client.ocr.process_async(**kwargs)


async def extract_from_text(
    text: str,
    *,
    schema_model: type[BaseModel],
    system_prompt: str,
) -> dict[str, Any]:
    """Structure already-OCR'd text with a chat completion.

    The fallback path for documents past the annotation page cap. Strictly worse
    than the single-call route — it costs a second request and the model works
    from flattened markdown with no layout information, which is precisely what
    tells a total apart from a line amount in a table.
    """
    client = get_mistral_client()

    try:
        response = await _call_chat(
            client,
            model=settings.MISTRAL_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            response_format=response_format_from_pydantic_model(schema_model),
        )
    except Exception as exc:
        logger.exception("Mistral chat extraction failed (%s)", type(exc).__name__)
        raise OcrError() from exc

    return _parse_json_content(response)


async def complete_json(
    *,
    system_prompt: str,
    user_content: str,
    schema_model: type[BaseModel],
) -> dict[str, Any]:
    """A structured chat completion. Used by the matching reranker."""
    client = get_mistral_client()

    try:
        response = await _call_chat(
            client,
            model=settings.MISTRAL_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=response_format_from_pydantic_model(schema_model),
        )
    except Exception as exc:
        logger.exception("Mistral completion failed (%s)", type(exc).__name__)
        raise OcrError() from exc

    return _parse_json_content(response)


@_retry
async def _call_chat(client: Mistral, **kwargs: Any) -> Any:
    return await client.chat.complete_async(**kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
#: Caps a runaway fractional part.
#:
#: Observed in production against mistral-ocr-latest: asked for `2 * 150.00`
#: under a strict JSON schema with a `number` type, the model emitted
#: `300.000000000000060663627080273979711532592773437500000...` — the full
#: binary expansion of the nearest float64 — and ran out of output budget
#: mid-number, truncating the JSON so it could never be parsed.
#:
#: Six fractional digits is far more than any currency needs, so trimming past
#: that loses nothing real and rescues the whole document.
_RUNAWAY_DECIMAL = re.compile(r"(\d+\.\d{6})\d{3,}")


def _scan(text: str) -> tuple[list[str], bool, int]:
    """Walk JSON text once. Returns (unclosed brackets, inside-a-string, last top comma)."""
    stack: list[str] = []
    in_string = False
    escaped = False
    last_comma = -1

    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            stack.append("]")
        elif char == "{":
            stack.append("}")
        elif char in "]}":
            if stack:
                stack.pop()
        elif char == ",":
            # Everything before a comma is a complete value, so this is the
            # furthest point the text can safely be rewound to.
            last_comma = index

    return stack, in_string, last_comma


def _repair_json(text: str) -> dict[str, Any] | None:
    """Best-effort salvage of a truncated or over-precise JSON response.

    Two passes, cheapest first:

      1. Trim runaway decimals. Usually enough on its own, because the runaway
         is what exhausted the budget.
      2. If the text is still cut short, rewind to the last complete value and
         close the open brackets. Returns a partial but *valid* object.

    A partial extraction is worth having: vendor and reference alone are enough
    to find candidate purchase orders, and a reviewer can see what was read.
    Returning nothing would throw away a document the model mostly understood.
    """
    trimmed = _RUNAWAY_DECIMAL.sub(r"\1", text)
    try:
        parsed = json.loads(trimmed)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    stack, in_string, last_comma = _scan(trimmed)
    if not stack and not in_string:
        return None  # malformed in a way rewinding cannot fix

    if last_comma > 0:
        head = trimmed[:last_comma]
        head_stack, head_in_string, _ = _scan(head)
        if not head_in_string:
            try:
                parsed = json.loads(head + "".join(reversed(head_stack)))
                if isinstance(parsed, dict):
                    logger.warning(
                        "Salvaged a truncated extraction (%d of %d chars kept)",
                        len(head),
                        len(text),
                    )
                    return parsed
            except json.JSONDecodeError:
                pass

    closed = (trimmed + '"' if in_string else trimmed) + "".join(reversed(stack))
    try:
        parsed = json.loads(closed)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    """Normalise whatever the SDK handed back into a plain dict.

    `document_annotation` comes back as a JSON string in some SDK versions and
    as a parsed object in others. Handling both means an SDK bump does not
    silently start storing the string "{...}" as the extraction.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed: Any = json.loads(value)
        except json.JSONDecodeError:
            repaired = _repair_json(value)
            if repaired is None:
                raise ExtractionInvalidError("The extraction was not valid JSON.")
            return repaired
        if not isinstance(parsed, dict):
            raise ExtractionInvalidError("The extraction was not a JSON object.")
        return parsed
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    raise ExtractionInvalidError("The extraction had an unrecognised shape.")


def _parse_json_content(response: Any) -> dict[str, Any]:
    """Pull the JSON body out of a chat completion."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise ExtractionInvalidError("The model returned an empty response.") from exc

    # Some models wrap JSON in a markdown fence despite being asked not to.
    if isinstance(content, str):
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        return _as_dict(text)

    return _as_dict(content)


def validate_extraction[T: BaseModel](payload: dict[str, Any], model: type[T]) -> T:
    """Parse a raw extraction through its schema.

    The single point where model output becomes trusted data. A ValidationError
    here is a failed extraction, not a 500 — the call worked, the content is
    unusable, and the row must record that rather than storing half of it.
    """
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning("Extraction failed validation: %s", exc.error_count())
        raise ExtractionInvalidError(
            f"The extraction did not match the expected schema "
            f"({exc.error_count()} field error(s))."
        ) from exc
