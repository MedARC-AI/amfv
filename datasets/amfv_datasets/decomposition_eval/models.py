"""Pydantic contracts and stable identities for decomposition evaluation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_serializer,
    model_validator,
)

__all__ = [
    "ClaimLabel",
    "ClaimPrediction",
    "DecompositionCase",
    "DecompositionPrediction",
    "ExtractedClaim",
    "FactDecompRow",
    "GenerationSettings",
    "GeneratorProvenance",
    "SourceSpan",
    "canonical_json",
    "external_id_for",
    "project_prediction",
    "prompt_hash_for",
    "resolve_prediction",
    "validate_claim_spans_against_response",
    "validate_identifier",
]

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
Identifier = Annotated[str, StringConstraints(pattern=_IDENTIFIER_PATTERN)]


def _require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def _require_schema_version(value: object) -> int:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be the integer 1")
    return value


NonblankText = Annotated[
    str,
    StringConstraints(strip_whitespace=False, min_length=1, max_length=1_000_000),
    AfterValidator(_require_nonblank),
]
ClaimText = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=20_000)]
SchemaVersion = Annotated[Literal[1], BeforeValidator(_require_schema_version)]
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimLabel(StrEnum):
    """Allowed importance label for one model or human claim."""

    VITAL = "vital"
    SUPPORTING = "supporting"
    PERIPHERAL = "peripheral"
    DUPLICATE = "duplicate"


class SourceSpan(_StrictModel):
    """Exact half-open Python code-point span in an assistant response."""

    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)
    text: ClaimText

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Reject empty or backward spans."""
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        if not self.text.strip():
            raise ValueError("span text must not be blank")
        return self


class ClaimPrediction(_StrictModel):
    """One claim, its exact source spans, and its proposed label."""

    claim: ClaimText
    spans: list[SourceSpan] = Field(min_length=1, max_length=100)
    label: ClaimLabel

    @model_validator(mode="after")
    def validate_claim_and_span_order(self) -> Self:
        """Reject blank claims and overlapping local spans."""
        if not self.claim.strip():
            raise ValueError("claim must not be blank")
        previous_end = -1
        for span in self.spans:
            if span.start < previous_end:
                raise ValueError("spans must be source-ordered and nonoverlapping within a claim")
            previous_end = span.end
        return self


class ExtractedClaim(_StrictModel):
    """One model-extracted claim with exact source quotations and no offsets."""

    claim: ClaimText
    source_texts: list[ClaimText] = Field(min_length=1, max_length=100)
    label: ClaimLabel

    @model_validator(mode="after")
    def validate_text(self) -> Self:
        """Reject blank claims and source quotations."""
        if not self.claim.strip():
            raise ValueError("claim must not be blank")
        if any(not source_text.strip() for source_text in self.source_texts):
            raise ValueError("source text must not be blank")
        return self


class DecompositionPrediction(_StrictModel):
    """Globally source-ordered claims returned by one model call."""

    claims: list[ExtractedClaim] = Field(max_length=10_000)


class DecompositionCase(_StrictModel):
    """One stable response input case with an optional query."""

    schema_version: SchemaVersion
    case_id: Identifier
    user_prompt: NonblankText | None = None
    assistant_response: NonblankText


class GenerationSettings(_StrictModel):
    """Stable generation values that affect model output."""

    max_tokens: int | None = Field(default=None, strict=True, ge=1)
    temperature: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    top_p: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    reasoning_effort: Literal["low", "medium", "high"] | None = None

    @model_serializer(mode="wrap")
    def omit_unset_values(self, handler: Any) -> dict[str, Any]:
        """Omit absent settings while preserving explicit provenance nulls."""
        return {key: value for key, value in handler(self).items() if value is not None}


class GeneratorProvenance(_StrictModel):
    """Nonsecret configuration required to reproduce one prediction."""

    model_id: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    model_revision: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    prompt_id: Identifier
    prompt_hash: Annotated[str, StringConstraints(pattern=_SHA256_PATTERN)]
    pydantic_ai_version: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    generation: GenerationSettings = Field(default_factory=GenerationSettings)

    @model_validator(mode="after")
    def validate_model_id(self) -> Self:
        """Reject model IDs that contain only whitespace."""
        if not self.model_id.strip():
            raise ValueError("model_id must not be blank")
        return self


class FactDecompRow(_StrictModel):
    """Version 1 FACT_DECOMP JSONL row consumed by the website importer."""

    schema_version: SchemaVersion
    eval_type: Literal["FACT_DECOMP"]
    external_id: Annotated[str, StringConstraints(pattern=_SHA256_PATTERN)]
    case_id: Identifier
    source: Literal["LLM"]
    user_prompt: NonblankText | None
    assistant_response: NonblankText
    arm_id: Identifier
    generator: GeneratorProvenance
    claims: list[ClaimPrediction] = Field(max_length=10_000)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Reject rows whose external identity does not match their keys."""
        expected = external_id_for(self.case_id, self.arm_id)
        if self.external_id != expected:
            raise ValueError(f"external_id must equal sha256(case_id + NUL + arm_id): {expected}")
        validate_claim_spans_against_response(self.assistant_response, self.claims)
        return self


def prompt_hash_for(prompt_bytes: bytes) -> str:
    """Return the SHA-256 digest of exact prompt bytes."""
    return hashlib.sha256(prompt_bytes).hexdigest()


def external_id_for(case_id: str, arm_id: str) -> str:
    """Return the stable case-and-arm identity after validating both IDs."""
    case_value = validate_identifier(case_id)
    arm_value = validate_identifier(arm_id)
    return hashlib.sha256(f"{case_value}\0{arm_value}".encode()).hexdigest()


def validate_identifier(value: str) -> str:
    """Return one validated safe identifier."""
    return _IDENTIFIER_ADAPTER.validate_python(value)


def validate_claim_spans_against_response(
    response: str,
    claims: Sequence[ClaimPrediction],
) -> Sequence[ClaimPrediction]:
    """Validate exact response spans and global claim ordering."""
    previous_first_start = -1
    for claim_index, claim in enumerate(claims):
        first_start = claim.spans[0].start
        if first_start < previous_first_start:
            raise ValueError(f"claim {claim_index} starts before the previous claim")
        previous_first_start = first_start
        for span_index, span in enumerate(claim.spans):
            if span.end > len(response):
                raise ValueError(f"claim {claim_index} span {span_index} ends outside the assistant response")
            if response[span.start : span.end] != span.text:
                raise ValueError(f"claim {claim_index} span {span_index} text does not match the assistant response")
    return claims


def resolve_prediction(response: str, prediction: DecompositionPrediction) -> list[ClaimPrediction]:
    """Resolve model-provided quotations to deterministic Python code-point spans."""
    resolved: list[ClaimPrediction] = []
    previous_first_start = 0
    for claim_index, extracted in enumerate(prediction.claims):
        spans: list[SourceSpan] = []
        search_start = previous_first_start
        for source_index, source_text in enumerate(extracted.source_texts):
            start = response.find(source_text, search_start)
            if start < 0:
                raise ValueError(
                    f"claim {claim_index} source text {source_index} is not an exact quotation "
                    "in source order from the assistant response"
                )
            next_start = response.find(source_text, start + 1)
            if next_start >= 0:
                raise ValueError(
                    f"claim {claim_index} source text {source_index} matches more than once in source order; "
                    "return a longer exact quotation that uniquely identifies the source"
                )
            end = start + len(source_text)
            spans.append(SourceSpan(start=start, end=end, text=source_text))
            search_start = end
        previous_first_start = spans[0].start
        resolved.append(ClaimPrediction(claim=extracted.claim, spans=spans, label=extracted.label))
    validate_claim_spans_against_response(response, resolved)
    return resolved


def project_prediction(
    case: DecompositionCase,
    prediction: DecompositionPrediction,
    *,
    arm_id: str,
    generator: GeneratorProvenance,
) -> FactDecompRow:
    """Project one validated prediction into the exact import row."""
    return FactDecompRow(
        schema_version=1,
        eval_type="FACT_DECOMP",
        external_id=external_id_for(case.case_id, arm_id),
        case_id=case.case_id,
        source="LLM",
        user_prompt=case.user_prompt,
        assistant_response=case.assistant_response,
        arm_id=arm_id,
        generator=generator,
        claims=resolve_prediction(case.assistant_response, prediction),
    )


def canonical_json(model: BaseModel) -> str:
    """Serialize one Pydantic model as deterministic compact UTF-8 JSON."""
    value: Any = model.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
