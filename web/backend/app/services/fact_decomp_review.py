"""Typed projection of imported FACT_DECOMP correction metadata."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import EvalFact, EvalItem, ItemSource
from app.schemas import (
    ClaimReview,
    HumanClaim,
    ImportanceGuide,
    ImportanceGuideLabel,
    ModelClaimLabel,
    ResponseClaimSpan,
    ReviewModelClaim,
)

__all__ = [
    "CorrectionMetadataError",
    "FactDecompCorrectionMetadata",
    "IMPORTANCE_RUBRIC_ID",
    "ModelCorrectionRating",
    "is_model_correction_item",
    "importance_guide",
    "project_model_claims",
    "read_correction_metadata",
]

MAX_CASE_ID_LENGTH = 128
MAX_ARM_ID_LENGTH = 128
MAX_CLAIMS = 10_000
MAX_CLAIM_TEXT_LENGTH = 20_000
MAX_SPANS_PER_CLAIM = 100
MAX_RESPONSE_LENGTH = 1_000_000
MAX_PROMPT_LENGTH = 100_000
IMPORTANCE_RUBRIC_ID = "importance-v1"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StoredCorrectionSpan(_StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_LENGTH)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        if not self.text.strip():
            raise ValueError("span text must not be blank")
        return self


class StoredCorrectionClaim(_StrictModel):
    claim: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_LENGTH)
    spans: list[StoredCorrectionSpan] = Field(
        min_length=1, max_length=MAX_SPANS_PER_CLAIM
    )
    label: ModelClaimLabel

    @field_validator("claim")
    @classmethod
    def validate_claim(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim must not be blank")
        return value

    @model_validator(mode="after")
    def validate_span_order(self) -> Self:
        previous_end = -1
        for span in self.spans:
            if span.start < previous_end:
                raise ValueError(
                    "spans must be source-ordered and nonoverlapping within a claim"
                )
            previous_end = span.end
        return self


class StoredCorrectionGenerator(_StrictModel):
    model_id: str = Field(min_length=1, max_length=500)
    model_revision: str | None = Field(default=None, min_length=1, max_length=500)
    prompt_text: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    pydantic_ai_version: str = Field(min_length=1, max_length=100)
    generation: dict[str, str | int | float] = Field(
        default_factory=dict, max_length=16
    )

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


class FactDecompCorrectionMetadata(_StrictModel):
    schema_version: Literal[2]
    review_mode: Literal["MODEL_LABEL_CORRECTION"]
    case_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=MAX_CASE_ID_LENGTH)
    arm_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=MAX_ARM_ID_LENGTH)
    canonical_row_sha256: str = Field(pattern=SHA256_PATTERN)
    generator: StoredCorrectionGenerator
    ordered_claim_annotations: list[StoredCorrectionClaim] = Field(
        max_length=MAX_CLAIMS
    )


class ModelCorrectionRating(_StrictModel):
    """The only persisted shape accepted for a correction review."""

    review_mode: Literal["MODEL_LABEL_CORRECTION"]
    schema_version: Literal[2]
    rubric_id: Literal["importance-v1"]
    claim_reviews: list[ClaimReview] = Field(max_length=MAX_CLAIMS)
    human_claims: list[HumanClaim] = Field(max_length=MAX_CLAIMS)
    coverage_checked: Literal[True]


def importance_guide() -> ImportanceGuide:
    """Return the backend-owned guide for extraction and importance review."""
    return ImportanceGuide(
        rubric_id=IMPORTANCE_RUBRIC_ID,
        instructions=[
            "Judge each claim by its contribution to the passage's purpose. Use the question as context when one is present.",
            "Flag changed meaning, lost context, unsuitable splitting or grouping, non-claims, and duplicates separately from importance.",
            "Add a missing claim only when it is a worthwhile assertion in the source text.",
            "Grade extraction and importance. Do not judge factual correctness.",
        ],
        labels=[
            ImportanceGuideLabel(
                value="vital",
                label="Vital",
                definition=(
                    "Essential to a substantive point, conclusion, or action, including information needed for correct "
                    "interpretation. Importance is independent of truth."
                ),
            ),
            ImportanceGuideLabel(
                value="semi-important",
                label="Semi-important",
                definition=(
                    "Useful substantive explanation, evidence, or context whose removal leaves the central point intact."
                ),
            ),
            ImportanceGuideLabel(
                value="unimportant",
                label="Unimportant",
                definition="An incidental claim that did not need to be extracted.",
            ),
        ],
    )


class CorrectionMetadataError(ValueError):
    """Raised when trusted correction metadata cannot be projected safely."""


def is_model_correction_item(item: EvalItem) -> bool:
    return (
        item.source == ItemSource.LLM
        and isinstance(item.item_metadata, dict)
        and item.item_metadata.get("review_mode") == "MODEL_LABEL_CORRECTION"
    )


def read_correction_metadata(item: EvalItem) -> FactDecompCorrectionMetadata:
    if not is_model_correction_item(item):
        raise CorrectionMetadataError("Item is not an imported model correction")
    try:
        return FactDecompCorrectionMetadata.model_validate(item.item_metadata)
    except ValueError as exc:
        raise CorrectionMetadataError(
            "Imported correction metadata is invalid"
        ) from exc


def project_model_claims(
    item: EvalItem, facts: list[EvalFact]
) -> tuple[FactDecompCorrectionMetadata, list[ReviewModelClaim]]:
    """Read and project one imported item for both reviewer and export APIs."""

    metadata = read_correction_metadata(item)
    if len(item.prompt_text) > MAX_RESPONSE_LENGTH or (
        item.lazy_query is not None and len(item.lazy_query) > MAX_RESPONSE_LENGTH
    ):
        raise CorrectionMetadataError(
            "Imported prompt or response exceeds the export limit"
        )
    ordered_facts = sorted(facts, key=lambda fact: fact.position)
    if len(metadata.ordered_claim_annotations) != len(ordered_facts):
        raise CorrectionMetadataError("Imported claim annotations do not match facts")
    claims: list[ReviewModelClaim] = []
    for fact, annotation in zip(
        ordered_facts, metadata.ordered_claim_annotations, strict=True
    ):
        spans = [
            ResponseClaimSpan.model_validate(span.model_dump())
            for span in annotation.spans
        ]
        for span in spans:
            if (
                span.end > len(item.prompt_text)
                or item.prompt_text[span.start : span.end] != span.text
            ):
                raise CorrectionMetadataError(
                    "Imported claim provenance does not match the response"
                )
        claims.append(
            ReviewModelClaim(
                claim_text=fact.fact_text,
                position=fact.position,
                response_spans=spans,
                proposed_label=annotation.label,
            )
        )
    return metadata, claims
