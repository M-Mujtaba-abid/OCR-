"""The model's verdict on which purchase order an invoice belongs to."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MatchAlternative(BaseModel):
    """A candidate the model considered and rejected, with its reason."""

    model_config = ConfigDict(populate_by_name=True)

    po_id: int = Field(description="The purchase order id from the candidate list.")
    why_not: str = Field(description="One sentence on why this is not the match.")


class MatchVerdict(BaseModel):
    """What the reranker returns.

    Every field is required — no Pydantic defaults — because a schema of
    all-optional fields lets the model legally omit them, and it does. Absent
    values are expressed as null instead.
    """

    matched_po_id: int | None = Field(
        description=(
            "The id of the single best matching purchase order, taken verbatim "
            "from the candidate list. null if none of them is the right order."
        )
    )
    confidence: float = Field(
        description="0-100. How certain you are. Be conservative."
    )
    reasoning: str = Field(
        description=(
            "Two or three sentences citing the specific evidence: vendor name, "
            "reference number, amounts, dates, line items."
        )
    )
    alternatives: list[MatchAlternative] = Field(
        description="Up to three runners-up and why each was rejected."
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> float:
        """Models return 0.87 as often as 87. Both mean the same thing."""
        try:
            value = float(v)
        except (TypeError, ValueError):
            return 0.0
        if 0.0 < value <= 1.0:
            value *= 100.0
        return max(0.0, min(100.0, value))

    @field_validator("reasoning", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return (str(v).strip() if v is not None else "")[:2000]

    @field_validator("alternatives", mode="before")
    @classmethod
    def _alts(cls, v: Any) -> Any:
        return v if isinstance(v, list) else []
