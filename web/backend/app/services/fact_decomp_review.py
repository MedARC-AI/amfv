"""Typed projection of imported FACT_DECOMP correction metadata."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.fact_decomp_contract import (
    IDENTIFIER_PATTERN,
    MAX_CLAIMS,
    MAX_RESPONSE_LENGTH,
    SHA256_PATTERN,
    GeneratorProvenance,
    ModelClaim,
)
from app.models import EvalFact, EvalItem, ItemSource
from app.schemas import (
    ClaimReview,
    HumanClaim,
    ImportanceGuide,
    ImportanceGuideLabel,
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

IMPORTANCE_RUBRIC_ID = "importance-v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FactDecompCorrectionMetadata(_StrictModel):
    schema_version: Literal[2]
    review_mode: Literal["MODEL_LABEL_CORRECTION"]
    case_id: str = Field(pattern=IDENTIFIER_PATTERN)
    arm_id: str = Field(pattern=IDENTIFIER_PATTERN)
    canonical_row_sha256: str = Field(pattern=SHA256_PATTERN)
    generator: GeneratorProvenance
    ordered_claim_annotations: list[ModelClaim] = Field(max_length=MAX_CLAIMS)


class ModelCorrectionRating(_StrictModel):
    """The only persisted shape accepted for a correction review."""

    review_mode: Literal["MODEL_LABEL_CORRECTION"]
    schema_version: Literal[2]
    rubric_id: Literal["importance-v1"]
    claim_reviews: list[ClaimReview] = Field(max_length=MAX_CLAIMS)
    human_claims: list[HumanClaim] = Field(max_length=MAX_CLAIMS)
    coverage_checked: Literal[True]


def importance_guide(*, authored: bool = False) -> ImportanceGuide:
    """Return the backend-owned guide for extraction and importance review."""
    guide = ImportanceGuide(
        rubric_id=IMPORTANCE_RUBRIC_ID,
        instructions=[
            "Check whether the extracted claims faithfully represent the source, can be verified independently, and capture its important content. Use the question, when provided, to understand the source’s purpose.",
            "Grade extraction quality and importance—not factual correctness. A false claim can still be correctly extracted and important.",
            "Preserve meaning: each claim must represent an assertion made by the source without adding information, correcting errors, or changing certainty.",
            "Stand on its own: a fact-checker must understand what to verify without seeing the original question, response, or other claims. Retain relevant subjects, populations, quantities, timeframes, qualifications, and attribution.",
            "Use one assessable assertion: separate assertions that could receive different factual judgments. Keep conditions, comparisons, and causal relationships together when splitting would change their meaning.",
            "Use Extraction issue and briefly explain changed meaning, missing context, unsuitable splitting or grouping, or text that does not express a factual assertion.",
            "Grade importance by contribution to the source’s substantive points, conclusions, or recommended actions. Keep the proposed grade if you agree; change it if you disagree. Do not use Unimportant as a substitute for an extraction flag.",
            "Mark Duplicate when a claim repeats information already captured by another claim without adding a meaningful distinction. Keep the first occurrence unflagged and mark later repetitions. No explanation is required.",
            "Different wording can express the same claim. Shared subject matter or partial overlap alone does not establish duplication. Different conditions, populations, quantities, timeframes, or qualifications can make claims distinct. Contradictory claims are not duplicates.",
            "Grade importance separately: a claim can be vital and duplicate.",
            "Check the source for missing worthwhile factual assertions. Add those claims, select their exact supporting passages, and assign an importance grade. Do not add every factual detail, repetitions, or assertions drawn only from the question.",
            "For each claim, select Looks good if its extraction and grade are acceptable and it is not a duplicate. Otherwise change its grade, mark Duplicate, mark Multiple Facts if the claim contains more than one atomic fact, or explain an Extraction issue. Untouched claims still need review. Reverting all changes requires a new decision.",
            "Before submitting, confirm that you reviewed every extracted claim and checked the source for missing worthwhile claims.",
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

    if authored:
        guide.instructions = [
            *guide.instructions[:5],
            "Use Malformed for extraction problems. Use Should list or Should not list to judge whether each fact belongs in the decomposition.",
            *guide.instructions[7:9],
            "Mark Duplicate separately from the reviewer call. A claim may belong in the decomposition but repeat an earlier claim.",
            "For each fact, select Looks good if its extraction and proposed call are acceptable and it is not a duplicate. Otherwise change its call, mark Duplicate, or mark Multiple Facts if it contains more than one atomic fact. Reverting all changes requires a new decision.",
            "Grade the whole decomposition for independent verifiability, removal of noise, and deduplication and ordering. Use Comments for missing assertions or other problems.",
        ]
    return guide


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
