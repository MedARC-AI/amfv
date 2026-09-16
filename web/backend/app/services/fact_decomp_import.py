"""Strict, idempotent import of generated FACT_DECOMP artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.fact_decomp_contract import (
    IDENTIFIER_PATTERN,
    MAX_CLAIMS,
    MAX_RESPONSE_LENGTH,
    SHA256_PATTERN,
    GeneratorProvenance,
    ModelClaim,
)
from app.models import (
    AuthorKind,
    Dataset,
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
    ItemSource,
    ItemStatus,
    ReviewTask,
)
from app.services.fact_decomp_review import FactDecompCorrectionMetadata

__all__ = [
    "FactDecompImportRowError",
    "FactDecompImportResult",
    "FactDecompImportRow",
    "canonical_row_hash",
    "external_id_for",
    "import_fact_decomp",
    "parse_fact_decomp_row",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FactDecompImportRow(_StrictModel):
    """Version 2 FACT_DECOMP item envelope emitted by the generator."""

    schema_version: Literal[2]
    eval_type: Literal["FACT_DECOMP"]
    external_id: str = Field(pattern=SHA256_PATTERN)
    case_id: str = Field(pattern=IDENTIFIER_PATTERN)
    source: Literal["LLM"]
    user_prompt: str | None = Field(
        default=None, min_length=1, max_length=MAX_RESPONSE_LENGTH
    )
    assistant_response: str = Field(min_length=1, max_length=MAX_RESPONSE_LENGTH)
    arm_id: str = Field(pattern=IDENTIFIER_PATTERN)
    generator: GeneratorProvenance
    claims: list[ModelClaim] = Field(max_length=MAX_CLAIMS)

    @field_validator("user_prompt", "assistant_response")
    @classmethod
    def validate_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_identity_and_spans(self) -> Self:
        expected = external_id_for(self.case_id, self.arm_id)
        if self.external_id != expected:
            raise ValueError(
                f"external_id must equal sha256(case_id + NUL + arm_id): {expected}"
            )
        previous_first_start = -1
        for claim_index, claim in enumerate(self.claims):
            first_start = claim.spans[0].start
            if first_start < previous_first_start:
                raise ValueError(
                    f"claim {claim_index} starts before the previous claim"
                )
            previous_first_start = first_start
            for span_index, span in enumerate(claim.spans):
                if span.end > len(self.assistant_response):
                    raise ValueError(
                        f"claim {claim_index} span {span_index} ends outside the assistant response"
                    )
                if self.assistant_response[span.start : span.end] != span.text:
                    raise ValueError(
                        f"claim {claim_index} span {span_index} text does not match the assistant response"
                    )
        return self


@dataclass(frozen=True)
class FactDecompImportResult:
    status: Literal["created", "unchanged"]


class FactDecompImportRowError(ValueError):
    """Raised when a row is invalid or would overwrite a different item."""


def parse_fact_decomp_row(payload: object) -> FactDecompImportRow:
    """Validate one decoded JSON object against the strict v2 contract."""
    if not isinstance(payload, dict):
        raise FactDecompImportRowError("row must be a JSON object")
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 2
    ):
        raise FactDecompImportRowError(
            f"unsupported schema_version {payload.get('schema_version')}; expected 2"
        )
    try:
        return FactDecompImportRow.model_validate(payload)
    except ValidationError as exc:
        # Keep line-local validation errors concise and independent of Pydantic's URL.
        errors = exc.errors(include_url=False)
        first = errors[0] if errors else {"loc": (), "msg": "invalid row"}
        location = ".".join(str(part) for part in first["loc"])
        raise FactDecompImportRowError(f"{location}: {first['msg']}") from exc


def import_fact_decomp(
    session: Session,
    *,
    dataset: Dataset,
    row: FactDecompImportRow,
    dry_run: bool = False,
) -> FactDecompImportResult:
    """Create one active model-label item, or identify an identical import."""
    if (
        dataset.id is None
        or not dataset.is_active
        or dataset.eval_type != EvalType.FACT_DECOMP
    ):
        raise FactDecompImportRowError("target must be an active FACT_DECOMP dataset")
    canonical_hash = canonical_row_hash(row)
    existing = _existing_item(
        session, dataset_id=dataset.id, external_id=row.external_id
    )
    if existing is not None:
        if _matches_import(existing, canonical_hash):
            return FactDecompImportResult(status="unchanged")
        raise FactDecompImportRowError(
            "external_id already exists with different content or provenance; replacement is not enabled"
        )
    if dry_run:
        return FactDecompImportResult(status="created")

    item_metadata = {
        "schema_version": 2,
        "review_mode": "MODEL_LABEL_CORRECTION",
        "case_id": row.case_id,
        "arm_id": row.arm_id,
        "canonical_row_sha256": canonical_hash,
        "generator": row.generator.model_dump(mode="json"),
        "ordered_claim_annotations": [
            claim.model_dump(mode="json") for claim in row.claims
        ],
    }
    try:
        item_metadata = FactDecompCorrectionMetadata.model_validate(
            item_metadata
        ).model_dump(mode="json")
    except ValueError as exc:
        raise FactDecompImportRowError(
            "generated correction metadata is invalid"
        ) from exc
    try:
        with session.begin_nested():
            item = EvalItem(
                dataset_id=dataset.id,
                external_id=row.external_id,
                eval_type=EvalType.FACT_DECOMP,
                source=ItemSource.LLM,
                author_kind=AuthorKind.LLM_GENERATED,
                generator_name=row.generator.model_id,
                prompt_text=row.assistant_response,
                lazy_query=row.user_prompt,
                item_metadata=item_metadata,
                status=ItemStatus.ACTIVE,
                is_active=True,
            )
            session.add(item)
            session.flush()
            assert item.id is not None
            for position, claim in enumerate(row.claims):
                session.add(
                    EvalFact(
                        item_id=item.id,
                        fact_text=claim.claim,
                        polarity=FactPolarity.SHOULD_LIST,
                        position=position,
                    )
                )
            session.add(ReviewTask(dataset_id=dataset.id, item_a_id=item.id))
            session.flush()
    except IntegrityError as exc:
        raced = _existing_item(
            session, dataset_id=dataset.id, external_id=row.external_id
        )
        if raced is not None and _matches_import(raced, canonical_hash):
            return FactDecompImportResult(status="unchanged")
        raise FactDecompImportRowError("FACT_DECOMP item could not be saved") from exc
    return FactDecompImportResult(status="created")


def canonical_row_hash(row: FactDecompImportRow) -> str:
    """Hash canonical JSON so key order and whitespace do not change identity."""
    payload = json.dumps(
        row.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def external_id_for(case_id: str, arm_id: str) -> str:
    """Return the stable case-and-arm SHA-256 identity."""
    return hashlib.sha256(f"{case_id}\0{arm_id}".encode()).hexdigest()


def _existing_item(
    session: Session, *, dataset_id: int, external_id: str
) -> EvalItem | None:
    return session.exec(
        select(EvalItem).where(
            col(EvalItem.dataset_id) == dataset_id,
            col(EvalItem.external_id) == external_id,
        )
    ).first()


def _matches_import(item: EvalItem, canonical_hash: str) -> bool:
    return (
        item.eval_type == EvalType.FACT_DECOMP
        and item.source == ItemSource.LLM
        and item.author_kind == AuthorKind.LLM_GENERATED
        and bool(item.item_metadata)
        and item.item_metadata.get("canonical_row_sha256") == canonical_hash
    )
