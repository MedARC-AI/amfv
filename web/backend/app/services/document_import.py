"""Generic, versioned JSONL source-document import behavior."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.models import Dataset, Document, EvalType
from app.schemas import ScrapedDocumentImportRow
from app.services.documents import create_document_with_chunks

DEFAULT_MAX_DOCUMENT_CONTENT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_SERIALIZED_METADATA_BYTES = 16 * 1024


class DocumentImportRowError(ValueError):
    """Raised when one JSONL row cannot be safely imported."""


@dataclass(frozen=True)
class DocumentImportResult:
    """The non-mutating or persisted disposition of one source document row."""

    status: Literal["created", "unchanged"]


def parse_scraped_document_row(
    payload: object,
    *,
    max_content_bytes: int = DEFAULT_MAX_DOCUMENT_CONTENT_BYTES,
    max_serialized_metadata_bytes: int = DEFAULT_MAX_SERIALIZED_METADATA_BYTES,
) -> ScrapedDocumentImportRow:
    """Validate one decoded JSON object against the frozen v1 import contract.

    Args:
        payload: Decoded JSON value from one JSONL line.

    Raises:
        DocumentImportRowError: If the row is not a supported versioned document.
    """
    if not isinstance(payload, dict):
        raise DocumentImportRowError("row must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int:
        raise DocumentImportRowError("schema_version must be an integer")
    if version != 1:
        raise DocumentImportRowError(
            f"unsupported schema_version {version}; expected 1"
        )
    try:
        row = ScrapedDocumentImportRow.model_validate(payload)
    except ValidationError as exc:
        raise DocumentImportRowError(_validation_message(exc)) from exc
    _validate_row_size_limits(
        row,
        max_content_bytes=max_content_bytes,
        max_serialized_metadata_bytes=max_serialized_metadata_bytes,
    )
    return row


def import_scraped_document(
    session: Session,
    *,
    dataset: Dataset,
    row: ScrapedDocumentImportRow,
    dry_run: bool,
) -> DocumentImportResult:
    """Create or identify one source document without silently replacing text.

    Args:
        session: Open database session.
        dataset: Active retrieval dataset selected by the import request.
        row: Validated versioned source-document row.
        dry_run: Whether to perform checks without writes.

    Raises:
        DocumentImportRowError: If the row would overwrite changed source text.
    """
    _validate_target_dataset(dataset)
    assert dataset.id is not None
    existing = _existing_document(
        session, dataset_id=dataset.id, external_id=row.external_id
    )
    if existing is not None:
        if existing.content == row.content:
            return DocumentImportResult(status="unchanged")
        raise DocumentImportRowError(
            "external_id already exists with different content; replacement is not enabled"
        )
    if dry_run:
        return DocumentImportResult(status="created")

    content_hash = source_content_hash(row.content)
    try:
        with session.begin_nested():
            create_document_with_chunks(
                session,
                dataset_id=dataset.id,
                title=row.title,
                content=row.content,
                external_id=row.external_id,
                source=row.source,
                source_url=row.url,
                source_metadata=_source_metadata(row),
                source_content_hash=content_hash,
            )
    except IntegrityError as exc:
        raced_document = _existing_document(
            session,
            dataset_id=dataset.id,
            external_id=row.external_id,
        )
        if raced_document is not None and raced_document.content == row.content:
            return DocumentImportResult(status="unchanged")
        raise DocumentImportRowError("document could not be saved") from exc
    return DocumentImportResult(status="created")


def source_content_hash(content: str) -> str:
    """Return the stable UTF-8 content hash used for idempotency evidence."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_target_dataset(dataset: Dataset) -> None:
    if (
        dataset.id is None
        or not dataset.is_active
        or dataset.eval_type != EvalType.RETRIEVAL
    ):
        raise DocumentImportRowError("target must be an active retrieval dataset")


def _existing_document(
    session: Session, *, dataset_id: int, external_id: str
) -> Document | None:
    return session.exec(
        select(Document).where(
            col(Document.dataset_id) == dataset_id,
            col(Document.external_id) == external_id,
        )
    ).first()


def _source_metadata(row: ScrapedDocumentImportRow) -> dict:
    """Preserve producer metadata while retaining the contract's section count."""
    return {**row.metadata, "section_count": row.section_count}


def _validate_row_size_limits(
    row: ScrapedDocumentImportRow,
    *,
    max_content_bytes: int,
    max_serialized_metadata_bytes: int,
) -> None:
    content_bytes = len(row.content.encode("utf-8"))
    if content_bytes > max_content_bytes:
        raise DocumentImportRowError(
            f"content exceeds the {max_content_bytes} byte limit"
        )
    try:
        metadata_bytes = len(
            json.dumps(
                row.metadata,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise DocumentImportRowError("metadata must be JSON serializable") from exc
    if metadata_bytes > max_serialized_metadata_bytes:
        raise DocumentImportRowError(
            f"metadata exceeds the {max_serialized_metadata_bytes} byte limit"
        )


def _validation_message(error: ValidationError) -> str:
    errors = error.errors(include_url=False)
    if not errors:
        return "row does not match the v1 document contract"
    first = errors[0]
    location = ".".join(str(part) for part in first["loc"])
    return f"{location}: {first['msg']}"


__all__ = [
    "DocumentImportResult",
    "DocumentImportRowError",
    "DEFAULT_MAX_DOCUMENT_CONTENT_BYTES",
    "DEFAULT_MAX_SERIALIZED_METADATA_BYTES",
    "import_scraped_document",
    "parse_scraped_document_row",
    "source_content_hash",
]
