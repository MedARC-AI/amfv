from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    AuthorKind,
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    ItemSource,
    ItemStatus,
    RetrievalSubmissionBatch,
    get_datetime_utc,
)
from app.schemas import (
    AuthoringConflict,
    AuthoringConflictResponse,
    AuthoringItemState,
    ChunkSummary,
    CreateFactDecompDraftSubmit,
    CreateRetrievalDraftSubmit,
    DatasetSummary,
    DocumentDetail,
    DocumentSummary,
    EvidenceSpan,
    FactDecompCreateResponse,
    FactDraft,
    RetrievalCreateResponse,
    RetrievalSubmissionBatchResponse,
    RetrievalSubmissionBatchSubmit,
    ValidationPreview,
)
from app.services.documents import (
    EvidenceSpanValidationError,
    documents_for_chunks,
    span_dicts,
    used_retrieval_document_ids,
    validate_evidence_spans,
)
from app.services.validation import validate_item

router = APIRouter(prefix="/create", tags=["create"])


@router.get("/options", response_model=list[DatasetSummary])
def read_create_options(session: SessionDep, current_user: CurrentUser) -> Any:
    _ = current_user
    datasets = session.exec(
        select(Dataset)
        .where(col(Dataset.is_active).is_(True))
        .order_by(col(Dataset.display_name))
    ).all()
    return [DatasetSummary.model_validate(dataset) for dataset in datasets]


@router.get("/source-documents", response_model=list[DocumentSummary])
def read_source_documents(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
    eval_type: EvalType,
    include_used: bool = False,
) -> Any:
    documents = session.exec(
        select(Document)
        .join(Dataset, col(Document.dataset_id) == col(Dataset.id))
        .where(
            col(Document.dataset_id) == dataset_id,
            col(Dataset.eval_type) == eval_type,
            col(Document.is_active) == True,  # noqa: E712
        )
        .order_by(col(Document.title))
    ).all()
    if include_used or eval_type != EvalType.RETRIEVAL:
        return [DocumentSummary.model_validate(document) for document in documents]

    used_document_ids = used_retrieval_document_ids(
        session, dataset_id=dataset_id, user_id=current_user.id
    )
    if used_document_ids:
        documents = [
            document
            for document in documents
            if document.id is None or document.id not in used_document_ids
        ]
    return [DocumentSummary.model_validate(document) for document in documents]


@router.get("/source-documents/{document_id}", response_model=DocumentDetail)
def read_source_document_detail(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: int,
    dataset_id: int,
    eval_type: EvalType,
) -> Any:
    _ = current_user
    document = session.exec(
        select(Document)
        .join(Dataset, col(Document.dataset_id) == col(Dataset.id))
        .where(
            col(Document.id) == document_id,
            col(Document.dataset_id) == dataset_id,
            col(Document.is_active) == True,  # noqa: E712
            col(Dataset.is_active) == True,  # noqa: E712
            col(Dataset.eval_type) == eval_type,
        )
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Source document not found")
    assert document.id is not None
    assert document.dataset_id is not None
    chunks = session.exec(
        select(Chunk)
        .where(col(Chunk.document_id) == document.id)
        .order_by(col(Chunk.position))
    ).all()
    return DocumentDetail(
        id=document.id,
        dataset_id=document.dataset_id,
        external_id=document.external_id,
        title=document.title,
        is_active=document.is_active,
        content=document.content,
        paragraphs=document.paragraphs,
        chunks=[ChunkSummary.model_validate(chunk) for chunk in chunks],
    )


@router.post("/retrieval/preview", response_model=ValidationPreview)
def preview_retrieval_creation(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
) -> ValidationPreview:
    _ = current_user
    return _preview_retrieval_validation(session, body)


@router.post("/retrieval/draft", response_model=RetrievalCreateResponse)
def create_retrieval_draft(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
) -> RetrievalCreateResponse:
    return _create_retrieval_item(session, body, current_user, status=ItemStatus.DRAFT)


@router.post("/retrieval/submit", response_model=RetrievalCreateResponse)
def submit_retrieval_draft(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
) -> RetrievalCreateResponse:
    return _create_retrieval_item(
        session, body, current_user, status=ItemStatus.SUBMITTED
    )


@router.post(
    "/retrieval/batch",
    response_model=RetrievalSubmissionBatchResponse,
    responses={409: {"model": AuthoringConflictResponse}},
)
def submit_retrieval_batch(
    session: SessionDep,
    body: RetrievalSubmissionBatchSubmit,
    current_user: CurrentUser,
) -> RetrievalSubmissionBatchResponse:
    request_hash = _canonical_retrieval_batch_hash(body.items)
    existing = _read_retrieval_batch_receipt(session, current_user.id, body.request_id)
    if existing is not None:
        return _reconcile_retrieval_batch(existing, request_hash)

    # Build and validate every item before adding the receipt or any item. This
    # keeps validation errors outside the transaction's write set.
    prepared_items = [
        _build_retrieval_item(
            session, item_body, current_user, status=ItemStatus.SUBMITTED
        )[0]
        for item_body in body.items
    ]
    _ensure_batch_prompts_are_distinct(prepared_items)
    receipt = RetrievalSubmissionBatch(
        author_user_id=current_user.id,
        request_id=body.request_id,
        request_hash=request_hash,
    )
    try:
        _persist_retrieval_batch(session, receipt, prepared_items)
    except IntegrityError:
        session.rollback()
        raced_receipt = _read_retrieval_batch_receipt(
            session, current_user.id, body.request_id
        )
        if raced_receipt is not None:
            return _reconcile_retrieval_batch(raced_receipt, request_hash)
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail="Retrieval batch could not be saved; no items were created.",
        ) from exc
    return _retrieval_batch_response(receipt, replayed=False)


@router.get(
    "/retrieval/batches/{request_id}",
    response_model=RetrievalSubmissionBatchResponse,
)
def read_retrieval_batch(
    session: SessionDep,
    request_id: str,
    current_user: CurrentUser,
) -> RetrievalSubmissionBatchResponse:
    receipt = _read_retrieval_batch_receipt(session, current_user.id, request_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Retrieval batch receipt not found")
    return _retrieval_batch_response(receipt, replayed=True)


@router.post("/fact-decomp/preview", response_model=ValidationPreview)
def preview_fact_decomp_creation(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> ValidationPreview:
    _ = current_user
    return _preview_fact_decomp_validation(session, body)


@router.post("/fact-decomp/draft", response_model=FactDecompCreateResponse)
def create_fact_decomp_draft(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> FactDecompCreateResponse:
    return _create_or_update_fact_decomp_item(
        session, body, current_user, status=ItemStatus.DRAFT
    )


@router.post(
    "/fact-decomp/submit",
    response_model=FactDecompCreateResponse,
    responses={409: {"model": AuthoringConflictResponse}},
)
def submit_fact_decomp_draft(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> FactDecompCreateResponse:
    return _create_or_update_fact_decomp_item(
        session, body, current_user, status=ItemStatus.SUBMITTED
    )


@router.get("/items/{item_id}", response_model=AuthoringItemState)
def read_authoring_item(
    session: SessionDep,
    item_id: int,
    current_user: CurrentUser,
) -> AuthoringItemState:
    item = session.get(EvalItem, item_id)
    if item is None or item.author_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Authored item not found")
    assert item.id is not None
    return AuthoringItemState(
        id=item.id,
        dataset_id=item.dataset_id,
        eval_type=item.eval_type,
        status=item.status,
        item_revision=item.revision,
    )


@router.post("/validate", response_model=ValidationPreview)
def validate_creation_request(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit | CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> ValidationPreview:
    """Compatibility preview endpoint backed by the same type-specific owners."""

    _ = current_user
    if isinstance(body, CreateRetrievalDraftSubmit):
        return _preview_retrieval_validation(session, body)
    return _preview_fact_decomp_validation(session, body)


def _create_retrieval_item(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
    *,
    status: ItemStatus,
) -> RetrievalCreateResponse:
    item, preview, document_ids = _build_retrieval_item(
        session, body, current_user, status=status
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return _retrieval_response(
        item,
        body.gold_evidence_spans,
        body.trap_evidence_spans,
        document_ids,
        preview,
    )


def _build_retrieval_item(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
    *,
    status: ItemStatus,
) -> tuple[EvalItem, ValidationPreview, list[int]]:
    _ensure_retrieval_dataset(session, body.dataset_id)
    requested_documents = _ensure_selected_documents(
        session, body.dataset_id, body.document_ids
    )
    preview = _preview_retrieval_validation(session, body)
    if status == ItemStatus.SUBMITTED and not preview.ok:
        _raise_validation_error(preview)

    try:
        chunks = validate_evidence_spans(
            session,
            dataset_id=body.dataset_id,
            spans=body.gold_evidence_spans + body.trap_evidence_spans,
            allowed_document_ids=set(body.document_ids) if body.document_ids else None,
        )
    except EvidenceSpanValidationError as exc:
        _raise_evidence_error(exc)
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


def _preview_retrieval_validation(
    session: SessionDep, body: CreateRetrievalDraftSubmit
) -> ValidationPreview:
    dataset_error = _retrieval_dataset_error(session, body.dataset_id)
    if dataset_error is not None:
        return _invalid_preview(dataset_error)
    documents = _read_active_documents(session, body.dataset_id, body.document_ids)
    if len(documents) != len(set(body.document_ids)):
        return _invalid_preview("One or more selected documents are unavailable.")
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
        # A client-supplied status must not influence authoring validation.
        status=ItemStatus.DRAFT,
    )
    result = validate_item(session, item, document=document, chunks=chunks)
    return ValidationPreview(ok=result.ok, flags=result.as_flags())


def _retrieval_dataset_error(session: SessionDep, dataset_id: int) -> str | None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active:
        return "Dataset is not active or does not exist."
    if dataset.eval_type != EvalType.RETRIEVAL:
        return "Dataset is not a retrieval dataset."
    return None


def _ensure_retrieval_dataset(session: SessionDep, dataset_id: int) -> None:
    error = _retrieval_dataset_error(session, dataset_id)
    if error is not None:
        _raise_validation_flags([{"level": "error", "message": error}])


def _ensure_selected_documents(
    session: SessionDep, dataset_id: int, document_ids: list[int]
) -> list[Document]:
    documents = _read_active_documents(session, dataset_id, document_ids)
    if len(documents) != len(set(document_ids)):
        _raise_validation_flags(
            [
                {
                    "level": "error",
                    "message": "One or more selected documents are unavailable.",
                }
            ]
        )
    return documents


def _read_active_documents(
    session: SessionDep, dataset_id: int, document_ids: list[int]
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


def _retrieval_response(
    item: EvalItem,
    gold_spans: list[EvidenceSpan],
    trap_spans: list[EvidenceSpan],
    document_ids: list[int],
    validation: ValidationPreview,
) -> RetrievalCreateResponse:
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


def _canonical_retrieval_batch_hash(items: list[CreateRetrievalDraftSubmit]) -> str:
    canonical_items: list[dict[str, Any]] = []
    for item in items:
        payload = item.model_dump(mode="json", exclude={"status"})
        payload["document_ids"] = sorted(set(payload["document_ids"]))
        canonical_items.append(payload)
    encoded = json.dumps(
        {"items": canonical_items},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_retrieval_batch_receipt(
    session: SessionDep,
    author_user_id: Any,
    request_id: str,
) -> RetrievalSubmissionBatch | None:
    return session.exec(
        select(RetrievalSubmissionBatch).where(
            col(RetrievalSubmissionBatch.author_user_id) == author_user_id,
            col(RetrievalSubmissionBatch.request_id) == request_id,
        )
    ).first()


def _reconcile_retrieval_batch(
    receipt: RetrievalSubmissionBatch, request_hash: str
) -> RetrievalSubmissionBatchResponse:
    if receipt.request_hash != request_hash:
        _raise_authoring_conflict(
            code="retrieval_batch_request_conflict",
            message="request_id was already used for a different retrieval batch.",
            request_id=receipt.request_id,
        )
    return _retrieval_batch_response(receipt, replayed=True)


def _retrieval_batch_response(
    receipt: RetrievalSubmissionBatch, *, replayed: bool
) -> RetrievalSubmissionBatchResponse:
    return RetrievalSubmissionBatchResponse(
        request_id=receipt.request_id,
        item_ids=[int(item_id) for item_id in receipt.created_item_ids],
        replayed=replayed,
    )


def _persist_retrieval_batch(
    session: SessionDep,
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


def _ensure_batch_prompts_are_distinct(items: list[EvalItem]) -> None:
    prompts = [item.prompt_text for item in items]
    if len(prompts) != len(set(prompts)):
        _raise_validation_flags(
            [
                {
                    "level": "error",
                    "message": "A retrieval batch cannot contain duplicate prompts.",
                }
            ]
        )


def _create_or_update_fact_decomp_item(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
    *,
    status: ItemStatus,
) -> FactDecompCreateResponse:
    _ensure_fact_decomp_dataset(session, body.dataset_id)
    if status == ItemStatus.SUBMITTED and body.item_id is None:
        _raise_authoring_conflict(
            code="draft_identity_required",
            message="Submit the saved draft by supplying item_id and expected_item_revision.",
        )
    existing = _read_owned_fact_item(session, body, current_user)
    document = _ensure_optional_source_document(
        session, body.dataset_id, body.document_id
    )
    try:
        _validate_fact_provenance(session, body, document)
    except EvidenceSpanValidationError as exc:
        _raise_evidence_error(exc)
    preview = _preview_fact_decomp_validation(session, body)
    if status == ItemStatus.SUBMITTED and not preview.ok:
        _raise_validation_error(preview)

    if existing is None:
        # New submissions begin as drafts in this transaction before the route
        # applies the server-owned submitted transition below.
        item = EvalItem(
            dataset_id=body.dataset_id,
            eval_type=EvalType.FACT_DECOMP,
            document_id=document.id if document is not None else None,
            source=ItemSource.HUMAN,
            author_kind=AuthorKind.HUMAN_LAY,
            author_user_id=current_user.id,
            prompt_text=body.source_text,
            evidence_spans=_fact_provenance_dicts(body.facts),
            item_metadata={"fact_provenance": _fact_provenance_dicts(body.facts)},
            status=ItemStatus.DRAFT,
            validation_flags=preview.flags,
        )
        session.add(item)
        session.flush()
        assert item.id is not None
        item.status = status
        assert item.id is not None
    else:
        item = _claim_fact_draft_update(
            session,
            body,
            current_user,
            document=document,
            validation=preview,
            status=status,
        )
        session.exec(delete(EvalFact).where(col(EvalFact.item_id) == item.id))

    assert item.id is not None
    item_id = item.id
    for fact in _fact_models(item_id, body.facts):
        session.add(fact)
    session.commit()
    session.expire_all()
    item = session.get(EvalItem, item_id)
    assert item is not None
    assert item.id is not None
    return FactDecompCreateResponse(
        id=item.id,
        dataset_id=item.dataset_id,
        eval_type=item.eval_type,
        status=item.status,
        prompt_text=item.prompt_text,
        document_id=item.document_id,
        facts=_ordered_facts(body.facts),
        item_revision=item.revision,
        validation=preview,
    )


def _read_owned_fact_item(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> EvalItem | None:
    if body.item_id is None:
        return None
    item = session.get(EvalItem, body.item_id)
    if (
        item is None
        or item.author_user_id != current_user.id
        or item.eval_type != EvalType.FACT_DECOMP
    ):
        raise HTTPException(
            status_code=404, detail="Fact-decomposition draft not found"
        )
    if item.dataset_id != body.dataset_id:
        _raise_authoring_conflict(
            code="item_dataset_conflict",
            message="A draft cannot be moved to a different dataset.",
            item_id=body.item_id,
        )
    return item


def _claim_fact_draft_update(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
    *,
    document: Document | None,
    validation: ValidationPreview,
    status: ItemStatus,
) -> EvalItem:
    """Claim an exact draft revision before replacing its facts.

    The predicate is the concurrency boundary: a second request with the same
    expected revision observes no returned row and cannot overwrite the first.
    """

    assert body.item_id is not None
    assert body.expected_item_revision is not None
    provenance = _fact_provenance_dicts(body.facts)
    updated_item_id = session.exec(
        update(EvalItem)
        .where(
            col(EvalItem.id) == body.item_id,
            col(EvalItem.author_user_id) == current_user.id,
            col(EvalItem.eval_type) == EvalType.FACT_DECOMP,
            col(EvalItem.dataset_id) == body.dataset_id,
            col(EvalItem.status) == ItemStatus.DRAFT,
            col(EvalItem.revision) == body.expected_item_revision,
        )
        .values(
            document_id=document.id if document is not None else None,
            prompt_text=body.source_text,
            evidence_spans=provenance,
            item_metadata={"fact_provenance": provenance},
            validation_flags=validation.flags,
            status=status,
            revision=col(EvalItem.revision) + 1,
            updated_at=get_datetime_utc(),
        )
        .returning(col(EvalItem.id))
    ).first()
    if updated_item_id is not None:
        item = session.get(EvalItem, updated_item_id)
        assert item is not None
        return item

    session.expire_all()
    current = session.get(EvalItem, body.item_id)
    if (
        current is None
        or current.author_user_id != current_user.id
        or current.eval_type != EvalType.FACT_DECOMP
    ):
        raise HTTPException(
            status_code=404, detail="Fact-decomposition draft not found"
        )
    if current.status != ItemStatus.DRAFT:
        _raise_authoring_conflict(
            code="item_not_editable",
            message="Only a draft can be saved or submitted.",
            item_id=body.item_id,
            expected_item_revision=body.expected_item_revision,
            actual_item_revision=current.revision,
        )
    _raise_authoring_conflict(
        code="item_revision_conflict",
        message="The draft was changed by another save; reload its current revision.",
        item_id=body.item_id,
        expected_item_revision=body.expected_item_revision,
        actual_item_revision=current.revision,
    )
    raise AssertionError("authoring conflict should have raised")


def _preview_fact_decomp_validation(
    session: SessionDep, body: CreateFactDecompDraftSubmit
) -> ValidationPreview:
    dataset_error = _fact_decomp_dataset_error(session, body.dataset_id)
    if dataset_error is not None:
        return _invalid_preview(dataset_error)
    document = _read_optional_source_document(
        session, body.dataset_id, body.document_id
    )
    if body.document_id is not None and document is None:
        return _invalid_preview("Source document is unavailable.")
    try:
        _validate_fact_provenance(session, body, document)
    except EvidenceSpanValidationError as exc:
        return ValidationPreview(
            ok=False,
            flags=[{"level": "error", "message": message} for message in exc.messages],
        )
    item = EvalItem(
        dataset_id=body.dataset_id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text=body.source_text,
        status=ItemStatus.DRAFT,
        id=body.item_id,
    )
    result = validate_item(session, item, facts=_fact_models(0, body.facts))
    return ValidationPreview(ok=result.ok, flags=result.as_flags())


def _fact_decomp_dataset_error(session: SessionDep, dataset_id: int) -> str | None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active:
        return "Dataset is not active or does not exist."
    if dataset.eval_type != EvalType.FACT_DECOMP:
        return "Dataset is not a fact-decomposition dataset."
    return None


def _ensure_fact_decomp_dataset(session: SessionDep, dataset_id: int) -> None:
    error = _fact_decomp_dataset_error(session, dataset_id)
    if error is not None:
        _raise_validation_flags([{"level": "error", "message": error}])


def _read_optional_source_document(
    session: SessionDep, dataset_id: int, document_id: int | None
) -> Document | None:
    if document_id is None:
        return None
    return session.exec(
        select(Document).where(
            col(Document.id) == document_id,
            col(Document.dataset_id) == dataset_id,
            col(Document.is_active) == True,  # noqa: E712
        )
    ).first()


def _ensure_optional_source_document(
    session: SessionDep, dataset_id: int, document_id: int | None
) -> Document | None:
    document = _read_optional_source_document(session, dataset_id, document_id)
    if document_id is not None and document is None:
        _raise_validation_flags(
            [{"level": "error", "message": "Source document is unavailable."}]
        )
    return document


def _validate_fact_provenance(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    document: Document | None,
) -> None:
    spans = [span for fact in body.facts for span in fact.provenance_spans]
    if not spans:
        return
    validate_evidence_spans(
        session,
        dataset_id=body.dataset_id,
        spans=spans,
        allowed_document_ids=(
            {document.id} if document is not None and document.id is not None else None
        ),
    )


def _fact_models(item_id: int, facts: list[FactDraft]) -> list[EvalFact]:
    return [
        EvalFact(
            item_id=item_id,
            fact_uuid=fact.fact_uuid,
            fact_text=fact.fact_text,
            polarity=fact.polarity,
            position=fact.position,
        )
        for fact in sorted(facts, key=lambda fact: fact.position)
    ]


def _ordered_facts(facts: list[FactDraft]) -> list[FactDraft]:
    return sorted(facts, key=lambda fact: fact.position)


def _fact_provenance_dicts(facts: list[FactDraft]) -> list[dict]:
    rows: list[dict] = []
    for fact in _ordered_facts(facts):
        for span in fact.provenance_spans:
            row = span.model_dump()
            row["fact_uuid"] = fact.fact_uuid
            rows.append(row)
    return rows


def _invalid_preview(message: str) -> ValidationPreview:
    return ValidationPreview(ok=False, flags=[{"level": "error", "message": message}])


def _raise_evidence_error(error: EvidenceSpanValidationError) -> None:
    _raise_validation_flags(
        [{"level": "error", "message": message} for message in error.messages]
    )


def _raise_validation_error(preview: ValidationPreview) -> None:
    _raise_validation_flags(preview.flags)


def _raise_validation_flags(flags: list[dict[str, str]]) -> None:
    raise HTTPException(status_code=400, detail=flags)


def _raise_authoring_conflict(
    *,
    code: str,
    message: str,
    item_id: int | None = None,
    expected_item_revision: int | None = None,
    actual_item_revision: int | None = None,
    request_id: str | None = None,
) -> None:
    detail = AuthoringConflict(
        code=code,
        message=message,
        item_id=item_id,
        expected_item_revision=expected_item_revision,
        actual_item_revision=actual_item_revision,
        request_id=request_id,
    )
    raise HTTPException(status_code=409, detail=detail.model_dump(exclude_none=True))
