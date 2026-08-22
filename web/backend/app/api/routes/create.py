from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import select

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
)
from app.schemas import (
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
        select(Dataset).where(Dataset.is_active == True).order_by(Dataset.display_name)  # noqa: E712
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
        .join(Dataset, Document.dataset_id == Dataset.id)
        .where(
            Document.dataset_id == dataset_id,
            Dataset.eval_type == eval_type,
            Document.is_active == True,  # noqa: E712
        )
        .order_by(Document.title)
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
        .join(Dataset, Document.dataset_id == Dataset.id)
        .where(
            Document.id == document_id,
            Document.dataset_id == dataset_id,
            Document.is_active == True,  # noqa: E712
            Dataset.is_active == True,  # noqa: E712
            Dataset.eval_type == eval_type,
        )
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Source document not found")
    chunks = session.exec(
        select(Chunk)
        .where(Chunk.document_id == document.id)
        .order_by(Chunk.position)
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


@router.post("/retrieval/draft", response_model=RetrievalCreateResponse)
def create_retrieval_draft(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
) -> Any:
    return _create_retrieval_item(session, body, current_user, status=ItemStatus.DRAFT)


@router.post("/retrieval/submit", response_model=RetrievalCreateResponse)
def submit_retrieval_draft(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit,
    current_user: CurrentUser,
) -> Any:
    return _create_retrieval_item(session, body, current_user, status=ItemStatus.ACTIVE)


@router.post("/fact-decomp/draft", response_model=FactDecompCreateResponse)
def create_fact_decomp_draft(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> Any:
    return _create_fact_decomp_item(session, body, current_user, status=ItemStatus.DRAFT)


@router.post("/fact-decomp/submit", response_model=FactDecompCreateResponse)
def submit_fact_decomp_draft(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> Any:
    return _create_fact_decomp_item(
        session, body, current_user, status=ItemStatus.SUBMITTED
    )


@router.post("/validate", response_model=ValidationPreview)
def validate_creation_request(
    session: SessionDep,
    body: CreateRetrievalDraftSubmit | CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
) -> Any:
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
    _ensure_retrieval_dataset(session, body.dataset_id)
    requested_documents = _ensure_selected_documents(
        session, body.dataset_id, body.document_ids
    )
    preview = _preview_retrieval_validation(session, body)
    if status != ItemStatus.DRAFT and not preview.ok:
        raise HTTPException(status_code=400, detail=preview.flags)

    try:
        chunks = validate_evidence_spans(
            session,
            dataset_id=body.dataset_id,
            spans=body.gold_evidence_spans + body.trap_evidence_spans,
            allowed_document_ids=set(body.document_ids) if body.document_ids else None,
        )
    except EvidenceSpanValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=[{"level": "error", "message": message} for message in exc.messages],
        ) from exc
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
    session.add(item)
    session.commit()
    session.refresh(item)
    return _retrieval_response(
        item, body.gold_evidence_spans, body.trap_evidence_spans, document_ids, preview
    )


def _preview_retrieval_validation(
    session: SessionDep, body: CreateRetrievalDraftSubmit
) -> ValidationPreview:
    dataset_error = _retrieval_dataset_error(session, body.dataset_id)
    if dataset_error is not None:
        return ValidationPreview(
            ok=False,
            flags=[{"level": "error", "message": dataset_error}],
        )
    documents = _read_active_documents(session, body.dataset_id, body.document_ids)
    if len(documents) != len(set(body.document_ids)):
        return ValidationPreview(
            ok=False,
            flags=[{"level": "error", "message": "One or more selected documents are unavailable."}],
        )
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
        status=body.status,
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
        raise HTTPException(
            status_code=400,
            detail=[{"level": "error", "message": error}],
        )


def _ensure_selected_documents(
    session: SessionDep, dataset_id: int, document_ids: list[int]
) -> list[Document]:
    documents = _read_active_documents(session, dataset_id, document_ids)
    if len(documents) != len(set(document_ids)):
        raise HTTPException(
            status_code=400,
            detail=[
                {
                    "level": "error",
                    "message": "One or more selected documents are unavailable.",
                }
            ],
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
                Document.id.in_(set(document_ids)),
                Document.dataset_id == dataset_id,
                Document.is_active == True,  # noqa: E712
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


def _create_fact_decomp_item(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    current_user: CurrentUser,
    *,
    status: ItemStatus,
) -> FactDecompCreateResponse:
    _ensure_fact_decomp_dataset(session, body.dataset_id)
    document = _ensure_optional_source_document(session, body.dataset_id, body.document_id)
    try:
        _validate_fact_provenance(session, body, document)
    except EvidenceSpanValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=[{"level": "error", "message": message} for message in exc.messages],
        ) from exc
    preview = _preview_fact_decomp_validation(session, body)
    if status == ItemStatus.SUBMITTED and not preview.ok:
        raise HTTPException(status_code=400, detail=preview.flags)

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
        status=status,
        validation_flags=preview.flags,
    )
    session.add(item)
    session.flush()
    facts = _fact_models(item.id, body.facts)
    for fact in facts:
        session.add(fact)
    session.commit()
    session.refresh(item)
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


def _preview_fact_decomp_validation(
    session: SessionDep, body: CreateFactDecompDraftSubmit
) -> ValidationPreview:
    dataset_error = _fact_decomp_dataset_error(session, body.dataset_id)
    if dataset_error is not None:
        return ValidationPreview(
            ok=False,
            flags=[{"level": "error", "message": dataset_error}],
        )
    document = _read_optional_source_document(session, body.dataset_id, body.document_id)
    if body.document_id is not None and document is None:
        return ValidationPreview(
            ok=False,
            flags=[{"level": "error", "message": "Source document is unavailable."}],
        )
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
        status=body.status,
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
        raise HTTPException(
            status_code=400,
            detail=[{"level": "error", "message": error}],
        )


def _read_optional_source_document(
    session: SessionDep, dataset_id: int, document_id: int | None
) -> Document | None:
    if document_id is None:
        return None
    return session.exec(
        select(Document).where(
            Document.id == document_id,
            Document.dataset_id == dataset_id,
            Document.is_active == True,  # noqa: E712
        )
    ).first()


def _ensure_optional_source_document(
    session: SessionDep, dataset_id: int, document_id: int | None
) -> Document | None:
    document = _read_optional_source_document(session, dataset_id, document_id)
    if document_id is not None and document is None:
        raise HTTPException(
            status_code=400,
            detail=[{"level": "error", "message": "Source document is unavailable."}],
        )
    return document


def _validate_fact_provenance(
    session: SessionDep,
    body: CreateFactDecompDraftSubmit,
    document: Document | None,
) -> None:
    spans = [
        span for fact in body.facts for span in fact.provenance_spans
    ]
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
