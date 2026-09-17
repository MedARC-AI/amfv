from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    AuthorKind,
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompSaveReceipt,
    ItemSource,
    ItemStatus,
    RetrievalSubmissionBatch,
)
from app.schemas import (
    AuthoringConflictResponse,
    AuthoringItemState,
    ChunkSummary,
    CreateFactDecompDraftSubmit,
    CreateRetrievalDraftSubmit,
    DatasetSummary,
    DocumentDetail,
    DocumentSummary,
    FactDecompCreateResponse,
    FactDecompSaveCommand,
    FactDecompSaveReceiptResponse,
    OwnedFactDecompItemDetail,
    OwnedFactDecompItemList,
    RetrievalCreateResponse,
    RetrievalSubmissionBatchResponse,
    RetrievalSubmissionBatchSubmit,
    ValidationPreview,
)
from app.services import authoring_fact_decomp, authoring_retrieval
from app.services.authoring_common import (
    raise_authoring_conflict,
    raise_evidence_error,
    raise_validation_error,
)
from app.services.documents import (
    EvidenceSpanValidationError,
    used_retrieval_document_ids,
)

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
        source_url=document.source_url,
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
    return authoring_retrieval.preview_retrieval_validation(session, body)


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
    request_hash = authoring_retrieval.canonical_retrieval_batch_hash(body.items)
    existing = authoring_retrieval.read_retrieval_batch_receipt(
        session, current_user.id, body.request_id
    )
    if existing is not None:
        return authoring_retrieval.reconcile_retrieval_batch(existing, request_hash)

    # Build and validate every item before adding the receipt or any item. This
    # keeps validation errors outside the transaction's write set.
    prepared_items = [
        authoring_retrieval.build_retrieval_item(
            session, item_body, current_user, status=ItemStatus.SUBMITTED
        )[0]
        for item_body in body.items
    ]
    authoring_retrieval.ensure_batch_prompts_are_distinct(prepared_items)
    receipt = RetrievalSubmissionBatch(
        author_user_id=current_user.id,
        request_id=body.request_id,
        request_hash=request_hash,
    )
    try:
        _persist_retrieval_batch(session, receipt, prepared_items)
    except IntegrityError:
        session.rollback()
        raced_receipt = authoring_retrieval.read_retrieval_batch_receipt(
            session, current_user.id, body.request_id
        )
        if raced_receipt is not None:
            return authoring_retrieval.reconcile_retrieval_batch(
                raced_receipt, request_hash
            )
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail="Retrieval batch could not be saved; no items were created.",
        ) from exc
    return authoring_retrieval.retrieval_batch_response(receipt, replayed=False)


@router.get(
    "/retrieval/batches/{request_id}",
    response_model=RetrievalSubmissionBatchResponse,
)
def read_retrieval_batch(
    session: SessionDep,
    request_id: str,
    current_user: CurrentUser,
) -> RetrievalSubmissionBatchResponse:
    receipt = authoring_retrieval.read_retrieval_batch_receipt(
        session, current_user.id, request_id
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="Retrieval batch receipt not found")
    return authoring_retrieval.retrieval_batch_response(receipt, replayed=True)


@router.post("/fact-decomp/preview", response_model=ValidationPreview)
def preview_fact_decomp_creation(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> ValidationPreview:
    _ = current_user
    return authoring_fact_decomp.preview_fact_decomp_validation(session, body)


@router.post(
    "/fact-decomp/draft",
    response_model=FactDecompCreateResponse,
    responses={409: {"model": AuthoringConflictResponse}},
)
def create_fact_decomp_draft(
    session: SessionDep,
    body: FactDecompSaveCommand,
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
    body: FactDecompSaveCommand,
    current_user: CurrentUser,
) -> FactDecompCreateResponse:
    return _create_or_update_fact_decomp_item(
        session, body, current_user, status=ItemStatus.SUBMITTED
    )


@router.get(
    "/fact-decomp/receipts/{request_id}",
    response_model=FactDecompSaveReceiptResponse,
)
def read_fact_decomp_save_receipt(
    session: SessionDep,
    request_id: str,
    current_user: CurrentUser,
) -> FactDecompSaveReceiptResponse:
    receipt = authoring_fact_decomp.read_fact_decomp_save_receipt(
        session, current_user.id, request_id
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="Fact save receipt not found")
    return authoring_fact_decomp.fact_decomp_save_receipt_response(
        receipt, replayed=True
    )


@router.get("/fact-decomp/items", response_model=OwnedFactDecompItemList)
def list_owned_fact_decomp_items(
    session: SessionDep,
    current_user: CurrentUser,
    status: ItemStatus = ItemStatus.DRAFT,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> OwnedFactDecompItemList:
    return authoring_fact_decomp.list_owned_fact_items(
        session, current_user.id, status=status, offset=offset, limit=limit
    )


@router.get("/fact-decomp/items/{item_id}", response_model=OwnedFactDecompItemDetail)
def read_owned_fact_decomp_item(
    session: SessionDep, current_user: CurrentUser, item_id: int
) -> OwnedFactDecompItemDetail:
    return authoring_fact_decomp.read_owned_fact_item_detail(
        session, current_user.id, item_id
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
        return authoring_retrieval.preview_retrieval_validation(session, body)
    return authoring_fact_decomp.preview_fact_decomp_validation(session, body)


def _create_retrieval_item(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
    *,
    status: ItemStatus,
) -> RetrievalCreateResponse:
    item, preview, document_ids = authoring_retrieval.build_retrieval_item(
        session, body, current_user, status=status
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return authoring_retrieval.retrieval_response(
        item,
        body.gold_evidence_spans,
        body.trap_evidence_spans,
        document_ids,
        preview,
    )


def _persist_retrieval_batch(
    session: SessionDep,
    receipt: RetrievalSubmissionBatch,
    items: list[EvalItem],
) -> None:
    """Keep the route-level persistence seam used by failure-injection tests."""

    authoring_retrieval.persist_retrieval_batch(session, receipt, items)


def _create_or_update_fact_decomp_item(
    session: SessionDep,
    body: FactDecompSaveCommand,
    current_user: CurrentUser,
    *,
    status: ItemStatus,
) -> FactDecompCreateResponse:
    command = "draft" if status == ItemStatus.DRAFT else "submit"
    request = CreateFactDecompDraftSubmit.model_validate(
        body.model_dump(exclude={"request_id"})
    )
    request_hash = authoring_fact_decomp.canonical_fact_save_hash(
        request, command=command
    )
    existing_receipt = authoring_fact_decomp.read_fact_decomp_save_receipt(
        session, current_user.id, body.request_id
    )
    if existing_receipt is not None:
        return authoring_fact_decomp.reconcile_fact_decomp_save(
            existing_receipt, request_hash
        ).response

    authoring_fact_decomp.ensure_fact_decomp_dataset(session, body.dataset_id)
    if status == ItemStatus.SUBMITTED and body.item_id is None:
        raise_authoring_conflict(
            code="draft_identity_required",
            message="Submit the saved draft by supplying item_id and expected_item_revision.",
        )
    existing = authoring_fact_decomp.read_owned_fact_item(session, body, current_user)
    document = authoring_fact_decomp.ensure_optional_source_document(
        session, body.dataset_id, body.document_id
    )
    try:
        authoring_fact_decomp.validate_fact_provenance(session, body, document)
    except EvidenceSpanValidationError as exc:
        raise_evidence_error(exc)
    preview = authoring_fact_decomp.preview_fact_decomp_validation(session, body)
    if status == ItemStatus.SUBMITTED and not preview.ok:
        raise_validation_error(preview)

    if existing is None:
        # New submissions begin as drafts in this transaction before the route
        # applies the server-owned submitted transition below.
        provenance = authoring_fact_decomp.fact_provenance_dicts(body.facts)
        item = EvalItem(
            dataset_id=body.dataset_id,
            eval_type=EvalType.FACT_DECOMP,
            document_id=document.id if document is not None else None,
            source=ItemSource.HUMAN,
            author_kind=AuthorKind.HUMAN_LAY,
            author_user_id=current_user.id,
            prompt_text=body.source_text,
            evidence_spans=provenance,
            item_metadata={"fact_provenance": provenance},
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
    for fact in authoring_fact_decomp.fact_models(item_id, body.facts):
        session.add(fact)
    session.flush()
    session.expire(item)
    session.refresh(item)
    response = FactDecompCreateResponse(
        id=item.id,
        dataset_id=item.dataset_id,
        eval_type=item.eval_type,
        status=item.status,
        prompt_text=item.prompt_text,
        document_id=item.document_id,
        facts=body.facts,
        item_revision=item.revision,
        validation=preview,
    )
    receipt = FactDecompSaveReceipt(
        author_user_id=current_user.id,
        request_id=body.request_id,
        request_hash=request_hash,
        command=command,
        item_id=item_id,
        request_payload=request.model_dump(mode="json"),
        response_payload=response.model_dump(mode="json"),
    )
    session.add(receipt)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raced_receipt = authoring_fact_decomp.read_fact_decomp_save_receipt(
            session, current_user.id, body.request_id
        )
        if raced_receipt is not None:
            return authoring_fact_decomp.reconcile_fact_decomp_save(
                raced_receipt, request_hash
            ).response
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=500,
            detail="Fact-decomposition save could not be committed.",
        ) from exc
    return response


def _claim_fact_draft_update(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
    *,
    document: Document | None,
    validation: ValidationPreview,
    status: ItemStatus,
) -> EvalItem:
    """Keep the route-level revision-claim seam used by concurrency tests."""

    return authoring_fact_decomp.claim_fact_draft_update(
        session,
        body,
        current_user,
        document=document,
        validation=validation,
        status=status,
    )
