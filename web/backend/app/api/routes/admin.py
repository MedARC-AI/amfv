import json
import uuid
from collections.abc import AsyncIterator
from itertools import combinations
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlmodel import col, func, select

from app.api.deps import (
    SessionDep,
    get_current_active_superuser,
    get_current_data_user,
)
from app.core.config import settings
from app.models import (
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompReview,
    ItemStatus,
    PooledCandidate,
    RelevanceJudgment,
    RetrievalQAReview,
    ReviewerKind,
    ReviewTask,
    User,
)
from app.schemas import (
    AdminAgreementMetric,
    AdminExport,
    AdminInterUserAgreementMetric,
    AdminItemSummary,
    AdminModerationAction,
    AdminTaskGenerationResult,
    AdminUserMetric,
    ChunkSummary,
    DatasetCreate,
    DatasetSummary,
    DocumentCreate,
    DocumentDetail,
    DocumentImportError,
    DocumentImportSummary,
    DocumentSummary,
)
from app.services import document_import
from app.services.agreement import (
    cohen_kappa,
    dataset_agreement,
    dataset_judgments_for_all,
    reviewer_mean_kappa,
)
from app.services.documents import (
    create_document_with_chunks,
    documents_for_chunks,
    resolve_item_chunks,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_data_user)],
)

IMPORT_READ_CHUNK_BYTES = 64 * 1024
MAX_IMPORT_ERROR_DETAILS = 100
MAX_IMPORT_ERROR_MESSAGE_CHARS = 1_024


class ImportArtifactTooLargeError(ValueError):
    """Raised when an upload crosses the configured artifact-size ceiling."""


@router.get("/datasets", response_model=list[DatasetSummary])
def read_admin_datasets(session: SessionDep) -> Any:
    datasets = session.exec(select(Dataset).order_by(col(Dataset.display_name))).all()
    return [DatasetSummary.model_validate(dataset) for dataset in datasets]


@router.post("/datasets", response_model=DatasetSummary)
def create_admin_dataset(session: SessionDep, body: DatasetCreate) -> Any:
    dataset = Dataset(
        name=body.name.strip(),
        display_name=body.display_name.strip(),
        description=body.description,
        eval_type=body.eval_type,
        is_active=body.is_active,
    )
    session.add(dataset)
    session.commit()
    session.refresh(dataset)
    return DatasetSummary.model_validate(dataset)


@router.get("/datasets/{dataset_id}", response_model=DatasetSummary)
def read_admin_dataset(session: SessionDep, dataset_id: int) -> Any:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return DatasetSummary.model_validate(dataset)


@router.post(
    "/datasets/{dataset_id}/generate-tasks", response_model=AdminTaskGenerationResult
)
def generate_dataset_tasks(session: SessionDep, dataset_id: int) -> Any:
    dataset = _get_dataset_or_404(session, dataset_id)
    assert dataset.id is not None
    if dataset.eval_type != EvalType.FACT_DECOMP:
        existing = session.exec(
            select(func.count(col(ReviewTask.id))).where(
                col(ReviewTask.dataset_id) == dataset.id
            )
        ).one()
        return AdminTaskGenerationResult(
            dataset_id=dataset.id,
            created=0,
            existing=existing,
        )
    item_ids = session.exec(
        select(col(EvalItem.id)).where(
            col(EvalItem.dataset_id) == dataset.id,
            col(EvalItem.eval_type) == EvalType.FACT_DECOMP,
            col(EvalItem.status) == ItemStatus.ACTIVE,
            col(EvalItem.is_active) == True,  # noqa: E712
        )
    ).all()
    existing_item_ids = set(
        session.exec(
            select(col(ReviewTask.item_a_id)).where(
                col(ReviewTask.dataset_id) == dataset.id
            )
        ).all()
    )
    created = 0
    for item_id in item_ids:
        assert item_id is not None
        if item_id in existing_item_ids:
            continue
        session.add(ReviewTask(dataset_id=dataset.id, item_a_id=item_id))
        created += 1
    session.commit()
    return AdminTaskGenerationResult(
        dataset_id=dataset.id,
        created=created,
        existing=len(existing_item_ids),
    )


@router.get("/documents", response_model=list[DocumentSummary])
def read_admin_documents(session: SessionDep) -> Any:
    documents = session.exec(select(Document).order_by(col(Document.title))).all()
    return [DocumentSummary.model_validate(document) for document in documents]


@router.post("/documents", response_model=DocumentDetail)
def create_admin_document(session: SessionDep, body: DocumentCreate) -> Any:
    dataset = _get_dataset_or_404(session, body.dataset_id)
    assert dataset.id is not None
    document = create_document_with_chunks(
        session,
        dataset_id=dataset.id,
        title=body.title,
        content=body.content,
        external_id=body.external_id,
    )
    session.commit()
    session.refresh(document)
    return _document_detail(session, document)


@router.post("/documents/import", response_model=DocumentImportSummary)
async def import_admin_documents(
    session: SessionDep,
    dataset_id: int = Form(...),
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    current_user: User = Depends(get_current_active_superuser),
) -> DocumentImportSummary:
    """Import a bounded stream of v1 source-document JSONL rows.

    Valid rows use nested transactions so one bad source document does not
    discard earlier valid rows. ``dry_run`` follows the same write and
    duplicate paths, then rolls the request transaction back before returning.
    """
    _ = current_user
    dataset = _get_dataset_or_404(session, dataset_id)
    if not dataset.is_active or dataset.eval_type != EvalType.RETRIEVAL:
        raise HTTPException(
            status_code=404, detail="Active retrieval dataset not found"
        )

    summary = DocumentImportSummary(
        created=0,
        unchanged=0,
        rejected=0,
        errors=[],
        dry_run=dry_run,
    )
    try:
        _ensure_import_transaction(session)
        async for line_number, raw_line in _iter_jsonl_lines(file):
            if not raw_line.strip():
                continue
            try:
                payload = _decode_jsonl_object(raw_line)
                row = document_import.parse_scraped_document_row(
                    payload,
                    max_content_bytes=settings.DOCUMENT_IMPORT_MAX_CONTENT_BYTES,
                    max_serialized_metadata_bytes=settings.DOCUMENT_IMPORT_MAX_METADATA_BYTES,
                )
                result = document_import.import_scraped_document(
                    session,
                    dataset=dataset,
                    row=row,
                    dry_run=False,
                )
            except (UnicodeDecodeError, document_import.DocumentImportRowError) as exc:
                _record_import_error(summary, line_number, str(exc))
                continue
            if result.status == "created":
                summary.created += 1
            else:
                summary.unchanged += 1
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except ImportArtifactTooLargeError as exc:
        session.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise
    finally:
        await file.close()
    return summary


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def read_admin_document(session: SessionDep, document_id: int) -> Any:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_detail(session, document)


@router.post("/documents/{document_id}/toggle", response_model=DocumentSummary)
def toggle_admin_document(session: SessionDep, document_id: int) -> Any:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    document.is_active = not document.is_active
    session.add(document)
    session.commit()
    session.refresh(document)
    return DocumentSummary.model_validate(document)


@router.get("/items", response_model=list[AdminItemSummary])
def read_admin_items(
    session: SessionDep,
    dataset_id: int | None = None,
    status: ItemStatus | None = ItemStatus.SUBMITTED,
) -> Any:
    statement = select(EvalItem).order_by(col(EvalItem.created_at).desc())
    if dataset_id is not None:
        statement = statement.where(col(EvalItem.dataset_id) == dataset_id)
    if status is not None:
        statement = statement.where(col(EvalItem.status) == status)
    items = session.exec(statement).all()
    return [_admin_item_summary(item) for item in items]


@router.post("/items/{item_id}/approve", response_model=AdminItemSummary)
def approve_admin_item(
    session: SessionDep, item_id: int, body: AdminModerationAction
) -> Any:
    item = _moderate_item(session, item_id, body, ItemStatus.ACTIVE)
    return _admin_item_summary(item)


@router.post("/items/{item_id}/reject", response_model=AdminItemSummary)
def reject_admin_item(
    session: SessionDep, item_id: int, body: AdminModerationAction
) -> Any:
    status = (
        ItemStatus.DRAFT if body.action == "return_to_draft" else ItemStatus.REJECTED
    )
    item = _moderate_item(session, item_id, body, status)
    return _admin_item_summary(item)


@router.get("/users")
def read_admin_users() -> Any:
    raise HTTPException(
        status_code=501, detail="Admin users API is not implemented yet"
    )


@router.patch("/users/{user_id}")
def update_admin_user(user_id: uuid.UUID) -> Any:
    _ = user_id
    raise HTTPException(
        status_code=501, detail="Admin user updates are not implemented yet"
    )


@router.get("/users/{user_id}/reviews")
def read_admin_user_reviews(user_id: uuid.UUID) -> Any:
    _ = user_id
    raise HTTPException(
        status_code=501, detail="User review history is not implemented yet"
    )


@router.get("/metrics/agreement", response_model=list[AdminAgreementMetric])
def read_agreement_metrics(
    session: SessionDep,
    dataset_id: int | None = None,
    reviewer_kind: ReviewerKind | None = None,
) -> Any:
    if dataset_id is None:
        datasets = session.exec(select(col(Dataset.id)).order_by(col(Dataset.id))).all()
        summaries = []
        for current_dataset_id in datasets:
            assert current_dataset_id is not None
            summaries.extend(
                dataset_agreement(
                    session, current_dataset_id, reviewer_kind=reviewer_kind
                )
            )
    else:
        _get_dataset_or_404(session, dataset_id)
        summaries = dataset_agreement(session, dataset_id, reviewer_kind=reviewer_kind)
    return [
        AdminAgreementMetric(
            dimension=summary.dimension,
            alpha=summary.alpha,
            n=summary.n,
        )
        for summary in summaries
    ]


@router.get("/metrics/users", response_model=list[AdminUserMetric])
def read_user_metrics(session: SessionDep, dataset_id: int | None = None) -> Any:
    users = session.exec(select(User).order_by(col(User.email))).all()
    return [_user_metric(session, user, dataset_id=dataset_id) for user in users]


@router.get(
    "/metrics/inter-user-agreement",
    response_model=list[AdminInterUserAgreementMetric],
)
def read_inter_user_agreement(
    session: SessionDep,
    dataset_id: int | None = None,
    min_overlap: int = Query(default=1, ge=1),
) -> Any:
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
    by_dimension = dataset_judgments_for_all(session, dataset_id=dataset_id)
    rows: list[AdminInterUserAgreementMetric] = []
    for dimension, judgments in sorted(by_dimension.items()):
        users = sorted(
            {user_id for labels in judgments.values() for user_id in labels},
            key=str,
        )
        for left_user_id, right_user_id in combinations(users, 2):
            left = {
                item_key: labels[left_user_id]
                for item_key, labels in judgments.items()
                if left_user_id in labels
            }
            right = {
                item_key: labels[right_user_id]
                for item_key, labels in judgments.items()
                if right_user_id in labels
            }
            kappa, overlap = cohen_kappa(left, right, min_overlap=min_overlap)
            if overlap < min_overlap:
                continue
            rows.append(
                AdminInterUserAgreementMetric(
                    dimension=dimension,
                    left_user_id=str(left_user_id),
                    right_user_id=str(right_user_id),
                    kappa=kappa,
                    overlap=overlap,
                )
            )
    return rows


@router.post("/ingest")
def ingest_dataset() -> Any:
    raise HTTPException(status_code=501, detail="Ingest is not implemented yet")


@router.get("/export", response_model=AdminExport)
def export_dataset(session: SessionDep, dataset_id: int | None = None) -> Any:
    statement = select(EvalItem).order_by(col(EvalItem.dataset_id), col(EvalItem.id))
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
        statement = statement.where(col(EvalItem.dataset_id) == dataset_id)
    items = session.exec(statement).all()
    return AdminExport(
        dataset_id=dataset_id,
        items=[_export_item(session, item) for item in items],
    )


def _get_dataset_or_404(session: SessionDep, dataset_id: int) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


def _ensure_import_transaction(session: SessionDep) -> None:
    """Start a physical SQLite transaction before row savepoints are released.

    SQLite does not begin a database transaction for a preceding ``SELECT``.
    Without this explicit ``BEGIN``, releasing the first row savepoint commits
    it, defeating artifact rollback and dry-run rollback semantics.
    """
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        driver_connection = connection.connection.driver_connection
        if not bool(getattr(driver_connection, "in_transaction", False)):
            connection.exec_driver_sql("BEGIN")


async def _iter_jsonl_lines(file: UploadFile) -> AsyncIterator[tuple[int, bytes]]:
    """Yield raw JSONL lines while enforcing the configured upload byte limit."""
    buffered = bytearray()
    artifact_bytes = 0
    line_number = 0
    while chunk := await file.read(IMPORT_READ_CHUNK_BYTES):
        artifact_bytes += len(chunk)
        if artifact_bytes > settings.DOCUMENT_IMPORT_MAX_ARTIFACT_BYTES:
            raise ImportArtifactTooLargeError(
                "artifact exceeds the "
                f"{settings.DOCUMENT_IMPORT_MAX_ARTIFACT_BYTES} byte limit"
            )
        buffered.extend(chunk)
        while (newline := buffered.find(b"\n")) >= 0:
            raw_line = bytes(buffered[:newline])
            del buffered[: newline + 1]
            line_number += 1
            yield line_number, raw_line
    if buffered:
        yield line_number + 1, bytes(buffered)


def _decode_jsonl_object(raw_line: bytes) -> object:
    """Decode one UTF-8 JSONL line into the value validated by the service."""
    try:
        return json.loads(raw_line.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise document_import.DocumentImportRowError(
            f"invalid JSON: {exc.msg}"
        ) from exc


def _record_import_error(
    summary: DocumentImportSummary,
    line_number: int,
    message: str,
) -> None:
    """Record a line rejection without allowing unbounded error response bodies."""
    summary.rejected += 1
    if len(summary.errors) < MAX_IMPORT_ERROR_DETAILS:
        summary.errors.append(
            DocumentImportError(
                line=line_number,
                message=(message or "invalid document row")[
                    :MAX_IMPORT_ERROR_MESSAGE_CHARS
                ],
            )
        )


def _document_detail(session: SessionDep, document: Document) -> DocumentDetail:
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


def _admin_item_summary(item: EvalItem) -> AdminItemSummary:
    assert item.id is not None
    return AdminItemSummary(
        id=item.id,
        dataset_id=item.dataset_id,
        eval_type=item.eval_type,
        status=item.status,
        prompt_text=item.prompt_text,
        category=item.category,
        document_id=item.document_id,
        revision=item.revision,
        validation_flags=item.validation_flags or [],
    )


def _moderate_item(
    session: SessionDep,
    item_id: int,
    body: AdminModerationAction,
    status: ItemStatus,
) -> EvalItem:
    item = session.get(EvalItem, item_id)
    if item is None or item.dataset_id != body.dataset_id:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.revision != body.expected_item_revision:
        raise HTTPException(status_code=409, detail="Item revision mismatch")
    if item.status != ItemStatus.SUBMITTED:
        raise HTTPException(
            status_code=400, detail="Only submitted items can be moderated"
        )
    if body.action == "approve" and status != ItemStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Moderation action/status mismatch")
    if body.action == "reject" and status != ItemStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Moderation action/status mismatch")
    if body.action == "return_to_draft" and status != ItemStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Moderation action/status mismatch")
    item.status = status
    item.revision += 1
    item.rejection_reason = (
        body.reason if status in {ItemStatus.REJECTED, ItemStatus.DRAFT} else None
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _export_item(session: SessionDep, item: EvalItem) -> dict:
    facts = session.exec(
        select(EvalFact)
        .where(col(EvalFact.item_id) == item.id)
        .order_by(col(EvalFact.position))
    ).all()
    chunks = resolve_item_chunks(session, item)
    documents = documents_for_chunks(session, chunks)
    review_task_count = session.exec(
        select(func.count(col(ReviewTask.id))).where(
            col(ReviewTask.item_a_id) == item.id
        )
    ).one()
    retrieval_reviews = session.exec(
        select(RetrievalQAReview)
        .where(col(RetrievalQAReview.item_id) == item.id)
        .order_by(col(RetrievalQAReview.id))
    ).all()
    return {
        "id": item.id,
        "dataset_id": item.dataset_id,
        "eval_type": item.eval_type,
        "status": item.status,
        "prompt_text": item.prompt_text,
        "expected_answer": item.expected_answer,
        "category": item.category,
        "evidence_spans": item.evidence_spans or [],
        "evidence_chunks": [
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "external_id": chunk.external_id,
                "position": chunk.position,
                "text": chunk.text,
            }
            for chunk in chunks
        ],
        "evidence_documents": [
            {
                "id": document.id,
                "external_id": document.external_id,
                "title": document.title,
            }
            for document in documents
        ],
        "facts": [
            {
                "fact_uuid": fact.fact_uuid,
                "fact_text": fact.fact_text,
                "polarity": fact.polarity,
                "position": fact.position,
            }
            for fact in facts
        ],
        "review_task_count": review_task_count,
        "retrieval_reviews": [
            {
                "id": review.id,
                "assignment_id": review.assignment_id,
                "user_id": str(review.user_id),
                "question_validity": review.question_validity,
                "evidence_quality": review.evidence_quality,
                "answer_correctness": review.answer_correctness,
                "answer_faithfulness": review.answer_faithfulness,
                "notes": review.notes,
                "verdict": review.verdict,
                "skipped": review.skipped,
                "skip_reason": review.skip_reason,
            }
            for review in retrieval_reviews
        ],
    }


def _user_metric(
    session: SessionDep,
    user: User,
    *,
    dataset_id: int | None,
) -> AdminUserMetric:
    authored_statement = select(func.count(col(EvalItem.id))).where(
        col(EvalItem.author_user_id) == user.id
    )
    fact_decomp_review_statement = select(func.count(col(FactDecompReview.id))).where(
        col(FactDecompReview.user_id) == user.id
    )
    retrieval_qa_review_statement = select(func.count(col(RetrievalQAReview.id))).where(
        col(RetrievalQAReview.user_id) == user.id
    )
    relevance_statement = select(func.count(col(RelevanceJudgment.id))).where(
        col(RelevanceJudgment.user_id) == user.id
    )
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
        authored_statement = authored_statement.where(
            col(EvalItem.dataset_id) == dataset_id
        )
        fact_decomp_review_statement = fact_decomp_review_statement.join(
            ReviewTask, col(FactDecompReview.task_id) == col(ReviewTask.id)
        ).where(col(ReviewTask.dataset_id) == dataset_id)
        retrieval_qa_review_statement = retrieval_qa_review_statement.join(
            EvalItem, col(RetrievalQAReview.item_id) == col(EvalItem.id)
        ).where(col(EvalItem.dataset_id) == dataset_id)
        relevance_statement = relevance_statement.join(
            PooledCandidate,
            col(RelevanceJudgment.candidate_id) == col(PooledCandidate.id),
        ).where(col(PooledCandidate.dataset_id) == dataset_id)
    mean_kappa, overlap = reviewer_mean_kappa(
        session, user.id, min_overlap=1, dataset_id=dataset_id
    )
    return AdminUserMetric(
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
        reviewer_kind=user.reviewer_kind.value,
        authored_items=session.exec(authored_statement).one(),
        fact_decomp_reviews=session.exec(fact_decomp_review_statement).one(),
        retrieval_qa_reviews=session.exec(retrieval_qa_review_statement).one(),
        relevance_judgments=session.exec(relevance_statement).one(),
        mean_kappa=mean_kappa,
        kappa_overlap=overlap,
    )
