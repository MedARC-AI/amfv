"""Shared FACT_DECOMP artifact primitives for import and stored provenance."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

__all__ = [
    "ClaimSpan",
    "ModelClaim",
    "GenerationSettings",
    "GeneratorProvenance",
    "ImportanceLabel",
    "MAX_CLAIMS",
    "MAX_CLAIM_TEXT_LENGTH",
    "MAX_SPANS_PER_CLAIM",
    "MAX_RESPONSE_LENGTH",
    "MAX_PROMPT_LENGTH",
    "IDENTIFIER_PATTERN",
    "SHA256_PATTERN",
]

ImportanceLabel = Literal["vital", "semi-important", "unimportant"]
MAX_CLAIMS = 10_000
MAX_CLAIM_TEXT_LENGTH = 20_000
MAX_SPANS_PER_CLAIM = 100
MAX_RESPONSE_LENGTH = 1_000_000
MAX_PROMPT_LENGTH = 100_000
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ClaimSpan(_StrictModel):
    """Exact half-open Python code-point span in the response."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_LENGTH)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        if not self.text.strip():
            raise ValueError("span text must not be blank")
        return self


class ModelClaim(_StrictModel):
    """One ordered generated claim with exact response provenance."""

    claim: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_LENGTH)
    spans: list[ClaimSpan] = Field(min_length=1, max_length=MAX_SPANS_PER_CLAIM)
    label: ImportanceLabel

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        if not self.claim.strip():
            raise ValueError("claim must not be blank")
        previous_end = -1
        for span in self.spans:
            if span.start < previous_end:
                raise ValueError(
                    "spans must be source-ordered and nonoverlapping within a claim"
                )
            previous_end = span.end
        return self


class GenerationSettings(_StrictModel):
    """Known generation settings retained as nonsecret provenance."""

    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    top_p: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    reasoning_effort: Literal["low", "medium", "high"] | None = None

    @model_serializer(mode="wrap")
    def omit_nulls(self, handler: Any) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class GeneratorProvenance(_StrictModel):
    """Reproduction metadata accepted from a generated artifact."""

    model_id: str = Field(min_length=1, max_length=500)
    model_revision: str | None = Field(default=None, min_length=1, max_length=500)
    prompt_text: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    pydantic_ai_version: str = Field(min_length=1, max_length=100)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model_id must not be blank")
        return value

    @field_validator("prompt_text")
    @classmethod
    def validate_prompt_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt_text must not be blank")
        return value
