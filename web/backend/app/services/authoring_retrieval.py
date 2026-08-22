from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session, col, select

from app.models import (
    AuthorKind,
    Dataset,
    Document,
    EvalItem,
    EvalType,
    ItemSource,
    ItemStatus,
    RetrievalSubmissionBatch,
    User,
)
from app.schemas import (
    CreateRetrievalDraftSubmit,
    EvidenceSpan,
    RetrievalCreateResponse,
    RetrievalSubmissionBatchResponse,
    ValidationPreview,
)
from app.services.authoring_common import (
    invalid_preview,
    raise_authoring_conflict,
    raise_evidence_error,
    raise_validation_error,
    raise_validation_flags,
)
from app.services.documents import (
    EvidenceSpanValidationError,
    documents_for_chunks,
    span_dicts,
    validate_evidence_spans,
)
from app.services.validation import validate_item

__all__ = [
    "build_retrieval_item",
    "canonical_retrieval_batch_hash",
    "ensure_batch_prompts_are_distinct",
    "persist_retrieval_batch",
    "preview_retrieval_validation",
    "read_retrieval_batch_receipt",
    "reconcile_retrieval_batch",
    "retrieval_batch_response",
    "retrieval_response",
]


def build_retrieval_item(
    session: Session,
    body: CreateRetrievalDraftSubmit,
    current_user: User,
    *,
    status: ItemStatus,
) -> tuple[EvalItem, ValidationPreview, list[int]]:
    """Build one validated retrieval item for a server-owned status transition."""

    _ensure_retrieval_dataset(session, body.dataset_id)
    requested_documents = _ensure_selected_documents(
        session, body.dataset_id, body.document_ids
    )
    preview = preview_retrieval_validation(session, body)
    if status == ItemStatus.SUBMITTED and not preview.ok:
        raise_validation_error(preview)

    try:
        chunks = validate_evidence_spans(
            session,
            dataset_id=body.dataset_id,
            spans=body.gold_evidence_spans + body.trap_evidence_spans,
            allowed_document_ids=set(body.document_ids) if body.document_ids else None,
        )
    except EvidenceSpanValidationError as exc:
        raise_evidence_error(exc)
    document_ids = sorted(
        {document.id for document in requested_documents if document.id is not None}
        | {chunk.document_id for chunk in chunks if chunk.document_id}
    )
    item = EvalItem(
        dataset_id=body.dataset_id,
        eval_type=EvalType.RETRIEVAL,
        category=body.category,
        document_id=document_ids[0] if len(document_ids) == 1 else None,
        source=ItemSource.HUMAN,
        author_kind=AuthorKind.HUMAN_LAY,
        author_user_id=current_user.id,
        prompt_text=body.question,
        expected_answer=body.expected_answer,
        evidence_spans=span_dicts(body.gold_evidence_spans, kind="gold")
        + span_dicts(body.trap_evidence_spans, kind="trap"),
        gold_chunk_ids=[span.chunk_id for span in body.gold_evidence_spans],
        trap_chunk_ids=[span.chunk_id for span in body.trap_evidence_spans],
        why_not_answerable=body.why_not_answerable,
        status=status,
        validation_flags=preview.flags,
    )
    return item, preview, document_ids


def preview_retrieval_validation(
    session: Session, body: CreateRetrievalDraftSubmit
) -> ValidationPreview:
    """Preview retrieval validation without persisting an authored item."""

    dataset_error = _retrieval_dataset_error(session, body.dataset_id)
    if dataset_error is not None:
        return invalid_preview(dataset_error)
    documents = _read_active_documents(session, body.dataset_id, body.document_ids)
    if len(documents) != len(set(body.document_ids)):
        return invalid_preview("One or more selected documents are unavailable.")
    try:
        chunks = validate_evidence_spans(
            session,
            dataset_id=body.dataset_id,
            spans=body.gold_evidence_spans + body.trap_evidence_spans,
            allowed_document_ids=set(body.document_ids) if body.document_ids else None,
        )
    except EvidenceSpanValidationError as exc:
        return ValidationPreview(
            ok=False,
            flags=[{"level": "error", "message": message} for message in exc.messages],
        )
    evidence_documents = documents_for_chunks(session, chunks)
    document = (
        evidence_documents[0]
        if len(evidence_documents) == 1
        else (documents[0] if len(documents) == 1 else None)
    )
    item = EvalItem(
        dataset_id=body.dataset_id,
        eval_type=EvalType.RETRIEVAL,
        category=body.category,
        source=ItemSource.HUMAN,
        prompt_text=body.question,
        expected_answer=body.expected_answer,
        evidence_spans=span_dicts(body.gold_evidence_spans, kind="gold")
        + span_dicts(body.trap_evidence_spans, kind="trap"),
        gold_chunk_ids=[span.chunk_id for span in body.gold_evidence_spans],
        trap_chunk_ids=[span.chunk_id for span in body.trap_evidence_spans],
        why_not_answerable=body.why_not_answerable,
        # Preview validation uses a non-eligible lifecycle state.
        status=ItemStatus.DRAFT,
    )
    result = validate_item(session, item, document=document, chunks=chunks)
    return ValidationPreview(ok=result.ok, flags=result.as_flags())


def retrieval_response(
    item: EvalItem,
    gold_spans: list[EvidenceSpan],
    trap_spans: list[EvidenceSpan],
    document_ids: list[int],
    validation: ValidationPreview,
) -> RetrievalCreateResponse:
    """Build the durable retrieval authoring response."""

    assert item.id is not None
    return RetrievalCreateResponse(
        id=item.id,
        dataset_id=item.dataset_id,
        eval_type=item.eval_type,
        category=item.category,
        status=item.status,
        prompt_text=item.prompt_text,
        expected_answer=item.expected_answer,
        evidence_spans=gold_spans,
        trap_evidence_spans=trap_spans,
        document_ids=document_ids,
        item_revision=item.revision,
        validation=validation,
    )


def canonical_retrieval_batch_hash(items: list[CreateRetrievalDraftSubmit]) -> str:
    """Hash the canonical retrieval batch request body."""

    canonical_items: list[dict[str, Any]] = []
    for item in items:
        payload = item.model_dump(mode="json")
        payload["document_ids"] = sorted(set(payload["document_ids"]))
        canonical_items.append(payload)
    encoded = json.dumps(
        {"items": canonical_items},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_retrieval_batch_receipt(
    session: Session,
    author_user_id: Any,
    request_id: str,
) -> RetrievalSubmissionBatch | None:
    """Read one author's retrieval batch receipt."""

    return session.exec(
        select(RetrievalSubmissionBatch).where(
            col(RetrievalSubmissionBatch.author_user_id) == author_user_id,
            col(RetrievalSubmissionBatch.request_id) == request_id,
        )
    ).first()


def reconcile_retrieval_batch(
    receipt: RetrievalSubmissionBatch, request_hash: str
) -> RetrievalSubmissionBatchResponse:
    """Return a matching receipt or raise the canonical idempotency conflict."""

    if receipt.request_hash != request_hash:
        raise_authoring_conflict(
            code="retrieval_batch_request_conflict",
            message="request_id was already used for a different retrieval batch.",
            request_id=receipt.request_id,
        )
    return retrieval_batch_response(receipt, replayed=True)


def retrieval_batch_response(
    receipt: RetrievalSubmissionBatch, *, replayed: bool
) -> RetrievalSubmissionBatchResponse:
    """Build the public retrieval batch receipt response."""

    return RetrievalSubmissionBatchResponse(
        request_id=receipt.request_id,
        item_ids=[int(item_id) for item_id in receipt.created_item_ids],
        replayed=replayed,
    )


def persist_retrieval_batch(
    session: Session,
    receipt: RetrievalSubmissionBatch,
    items: list[EvalItem],
) -> None:
    """Flush receipt and items together so a failure leaves no partial batch."""

    session.add(receipt)
    for item in items:
        session.add(item)
    session.flush()
    receipt.created_item_ids = [item.id for item in items if item.id is not None]
    if len(receipt.created_item_ids) != len(items):
        raise RuntimeError("A retrieval batch item did not receive an id")
    session.add(receipt)
    session.commit()
    session.refresh(receipt)


def ensure_batch_prompts_are_distinct(items: list[EvalItem]) -> None:
    """Reject a retrieval batch that repeats one prompt."""

    prompts = [item.prompt_text for item in items]
    if len(prompts) != len(set(prompts)):
        raise_validation_flags(
            [
                {
                    "level": "error",
                    "message": "A retrieval batch cannot contain duplicate prompts.",
                }
            ]
        )


def _retrieval_dataset_error(session: Session, dataset_id: int) -> str | None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active:
        return "Dataset is not active or does not exist."
    if dataset.eval_type != EvalType.RETRIEVAL:
        return "Dataset is not a retrieval dataset."
    return None


def _ensure_retrieval_dataset(session: Session, dataset_id: int) -> None:
    error = _retrieval_dataset_error(session, dataset_id)
    if error is not None:
        raise_validation_flags([{"level": "error", "message": error}])


def _ensure_selected_documents(
    session: Session, dataset_id: int, document_ids: list[int]
) -> list[Document]:
    documents = _read_active_documents(session, dataset_id, document_ids)
    if len(documents) != len(set(document_ids)):
        raise_validation_flags(
            [
                {
                    "level": "error",
                    "message": "One or more selected documents are unavailable.",
                }
            ]
        )
    return documents


def _read_active_documents(
    session: Session, dataset_id: int, document_ids: list[int]
) -> list[Document]:
    if not document_ids:
        return []
    return list(
        session.exec(
            select(Document).where(
                col(Document.id).in_(set(document_ids)),
                col(Document.dataset_id) == dataset_id,
                col(Document.is_active) == True,  # noqa: E712
            )
        ).all()
    )
