import json
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import String, cast
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
    RetrievalQAReview,
    ReviewerKind,
    ReviewTask,
    User,
)
from app.schemas import (
    AdminAgreementMetric,
    AdminExport,
    AdminExportAuthoredItem,
    AdminExportItem,
    AdminExportModelCorrectionItem,
    AdminInterUserAgreementMetric,
    AdminItemSummary,
    AdminModerationAction,
    AdminTaskGenerationResult,
    AdminUserMetricPage,
    ChunkSummary,
    DatasetCreate,
    DatasetSummary,
    DocumentCreate,
    DocumentDetail,
    DocumentImportError,
    DocumentImportSummary,
    DocumentSummary,
    FactDecompImportSummary,
)
from app.services import document_import, fact_decomp_import
from app.services.admin_metrics import user_activity_metrics
from app.services.agreement import (
    JudgmentVolumeExceeded,
    agreement_summaries,
    cohen_kappa,
    dataset_judgments_for_all,
)
from app.services.documents import (
    create_document_with_chunks,
)
from app.services.fact_decomp_review import (
    CorrectionMetadataError,
    ModelCorrectionRating,
    is_model_correction_item,
    project_model_claims,
    read_correction_metadata,
)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_data_user)],
)

IMPORT_READ_CHUNK_BYTES = 64 * 1024
MAX_IMPORT_ERROR_DETAILS = 100
MAX_IMPORT_ERROR_MESSAGE_CHARS = 1_024
DEFAULT_ADMIN_PAGE_SIZE = 100
MAX_ADMIN_METRIC_PAGE_SIZE = 500
MAX_ADMIN_EXPORT_PAGE_SIZE = 1_000
MAX_ADMIN_EXPORT_REVIEW_ROWS = 5_000
MAX_ADMIN_EXPORT_SERIALIZED_BYTES = 16 * 1024 * 1024
MAX_SYNC_AGREEMENT_JUDGMENTS = 5_000


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
                row = document_import.parse_source_document_row(
                    payload,
                    max_content_bytes=settings.DOCUMENT_IMPORT_MAX_CONTENT_BYTES,
                    max_serialized_metadata_bytes=settings.DOCUMENT_IMPORT_MAX_METADATA_BYTES,
                )
                result = document_import.import_source_document(
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
    max_judgments: int = Query(
        default=2_000,
        ge=1,
        le=MAX_SYNC_AGREEMENT_JUDGMENTS,
    ),
) -> Any:
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
    try:
        summaries = agreement_summaries(
            dataset_judgments_for_all(
                session,
                dataset_id=dataset_id,
                reviewer_kind=reviewer_kind,
                max_rows=max_judgments,
            )
        )
    except JudgmentVolumeExceeded as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [
        AdminAgreementMetric(
            dimension=summary.dimension,
            alpha=summary.alpha,
            n=summary.n,
        )
        for summary in summaries
    ]


@router.get("/metrics/users", response_model=AdminUserMetricPage)
def read_user_metrics(
    session: SessionDep,
    dataset_id: int | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=DEFAULT_ADMIN_PAGE_SIZE,
        ge=1,
        le=MAX_ADMIN_METRIC_PAGE_SIZE,
    ),
) -> Any:
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
    total = session.exec(select(func.count(col(User.id)))).one()
    users = session.exec(
        select(User).order_by(col(User.email)).offset(offset).limit(limit)
    ).all()
    items = user_activity_metrics(session, users, dataset_id=dataset_id)
    return AdminUserMetricPage(
        offset=offset,
        limit=limit,
        total=total,
        next_offset=offset + len(users) if offset + len(users) < total else None,
        items=items,
    )


@router.get(
    "/metrics/inter-user-agreement",
    response_model=list[AdminInterUserAgreementMetric],
)
def read_inter_user_agreement(
    session: SessionDep,
    left_user_id: uuid.UUID,
    right_user_id: uuid.UUID,
    dataset_id: int | None = None,
    min_overlap: int = Query(default=1, ge=1),
    max_judgments: int = Query(
        default=2_000,
        ge=1,
        le=MAX_SYNC_AGREEMENT_JUDGMENTS,
    ),
) -> Any:
    if left_user_id == right_user_id:
        raise HTTPException(status_code=422, detail="Choose two different reviewers")
    if (
        session.get(User, left_user_id) is None
        or session.get(User, right_user_id) is None
    ):
        raise HTTPException(status_code=404, detail="Reviewer not found")
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
    try:
        by_dimension = dataset_judgments_for_all(
            session,
            dataset_id=dataset_id,
            user_ids={left_user_id, right_user_id},
            max_rows=max_judgments,
        )
    except JudgmentVolumeExceeded as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows: list[AdminInterUserAgreementMetric] = []
    for dimension, judgments in sorted(by_dimension.items()):
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


@router.post("/ingest", response_model=FactDecompImportSummary)
async def ingest_dataset(
    session: SessionDep,
    dataset_id: int = Form(...),
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    current_user: User = Depends(get_current_active_superuser),
) -> FactDecompImportSummary:
    """Import bounded, versioned FACT_DECOMP JSONL rows."""
    _ = current_user
    dataset = _get_dataset_or_404(session, dataset_id)
    if not dataset.is_active or dataset.eval_type != EvalType.FACT_DECOMP:
        raise HTTPException(
            status_code=404, detail="Active FACT_DECOMP dataset not found"
        )

    summary = FactDecompImportSummary(
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
                row = fact_decomp_import.parse_fact_decomp_row(payload)
                result = fact_decomp_import.import_fact_decomp(
                    session,
                    dataset=dataset,
                    row=row,
                    # Keep preview rows visible to later lines so duplicate and
                    # conflict checks match a real import. The request-level
                    # rollback below keeps the preview side-effect free.
                    dry_run=False,
                )
            except (
                UnicodeDecodeError,
                document_import.DocumentImportRowError,
                fact_decomp_import.FactDecompImportRowError,
            ) as exc:
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


@router.get("/export", response_model=AdminExport)
def export_dataset(
    session: SessionDep,
    dataset_id: int | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=DEFAULT_ADMIN_PAGE_SIZE,
        ge=1,
        le=MAX_ADMIN_EXPORT_PAGE_SIZE,
    ),
) -> Any:
    statement = select(EvalItem.id).order_by(col(EvalItem.dataset_id), col(EvalItem.id))
    count_statement = select(func.count(col(EvalItem.id)))
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
        statement = statement.where(col(EvalItem.dataset_id) == dataset_id)
        count_statement = count_statement.where(col(EvalItem.dataset_id) == dataset_id)
    total = session.exec(count_statement).one()
    item_ids = list(session.exec(statement.offset(offset).limit(limit)).all())
    _ensure_export_content_budget(session, item_ids)
    items = (
        session.exec(
            select(EvalItem)
            .where(col(EvalItem.id).in_(item_ids))
            .order_by(col(EvalItem.dataset_id), col(EvalItem.id))
        ).all()
        if item_ids
        else []
    )
    exported_items = _export_items(session, items)
    serialized_bytes = sum(
        len(item.model_dump_json().encode("utf-8")) for item in exported_items
    )
    if serialized_bytes > MAX_ADMIN_EXPORT_SERIALIZED_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "Export exceeds the "
                f"{MAX_ADMIN_EXPORT_SERIALIZED_BYTES}-byte serialized-content limit"
            ),
        )
    return AdminExport(
        dataset_id=dataset_id,
        offset=offset,
        limit=limit,
        total=total,
        next_offset=offset + len(items) if offset + len(items) < total else None,
        items=exported_items,
    )


def _ensure_export_content_budget(session: SessionDep, item_ids: list[int]) -> None:
    """Reject oversized pages before loading claim-heavy JSON and fact rows."""
    if not item_ids:
        return
    item_characters = session.exec(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(func.length(EvalItem.prompt_text), 0)
                    + func.coalesce(func.length(EvalItem.lazy_query), 0)
                    + func.coalesce(func.length(EvalItem.expected_answer), 0)
                    + func.coalesce(
                        func.length(cast(EvalItem.item_metadata, String)), 0
                    )
                    + func.coalesce(
                        func.length(cast(EvalItem.evidence_spans, String)), 0
                    )
                ),
                0,
            )
        ).where(col(EvalItem.id).in_(item_ids))
    ).one()
    fact_characters = session.exec(
        select(func.coalesce(func.sum(func.length(EvalFact.fact_text)), 0)).where(
            col(EvalFact.item_id).in_(item_ids)
        )
    ).one()
    review_characters = session.exec(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        func.length(cast(FactDecompReview.ratings, String)), 0
                    )
                    + func.coalesce(func.length(FactDecompReview.comment), 0)
                    + func.coalesce(
                        func.length(cast(FactDecompReview.flags, String)), 0
                    )
                ),
                0,
            )
        )
        .join(ReviewTask, col(FactDecompReview.task_id) == col(ReviewTask.id))
        .where(col(ReviewTask.item_a_id).in_(item_ids))
    ).one()
    # Four bytes per code point is a conservative UTF-8 upper bound. The exact
    # serialized-size check below accounts for JSON punctuation and escaping.
    estimated_bytes = 4 * int(item_characters + fact_characters + review_characters)
    if estimated_bytes > MAX_ADMIN_EXPORT_SERIALIZED_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "Export exceeds the "
                f"{MAX_ADMIN_EXPORT_SERIALIZED_BYTES}-byte content limit"
            ),
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
    summary: DocumentImportSummary | FactDecompImportSummary,
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


def _export_items(
    session: SessionDep, items: Sequence[EvalItem]
) -> list[AdminExportItem]:
    """Serialize one bounded export page with a fixed query count."""
    item_ids = [item.id for item in items if item.id is not None]
    if not item_ids:
        return []

    facts_by_item: defaultdict[int, list[EvalFact]] = defaultdict(list)
    for fact in session.exec(
        select(EvalFact)
        .where(col(EvalFact.item_id).in_(item_ids))
        .order_by(col(EvalFact.item_id), col(EvalFact.position))
    ).all():
        facts_by_item[fact.item_id].append(fact)

    chunk_ids_by_item = {
        item.id: _item_chunk_ids(item) for item in items if item.id is not None
    }
    all_chunk_ids = {
        chunk_id for chunk_ids in chunk_ids_by_item.values() for chunk_id in chunk_ids
    }
    chunks_by_id = (
        {
            chunk.id: chunk
            for chunk in session.exec(
                select(Chunk).where(col(Chunk.id).in_(all_chunk_ids))
            ).all()
            if chunk.id is not None
        }
        if all_chunk_ids
        else {}
    )
    document_ids = {chunk.document_id for chunk in chunks_by_id.values()}
    documents_by_id = (
        {
            document.id: document
            for document in session.exec(
                select(Document).where(col(Document.id).in_(document_ids))
            ).all()
            if document.id is not None
        }
        if document_ids
        else {}
    )
    tasks_by_item: defaultdict[int, list[ReviewTask]] = defaultdict(list)
    fact_reviews_by_task: defaultdict[int, list[FactDecompReview]] = defaultdict(list)
    task_review_rows = session.exec(
        select(ReviewTask, FactDecompReview)
        .join(
            FactDecompReview,
            col(FactDecompReview.task_id) == col(ReviewTask.id),
            isouter=True,
        )
        .where(col(ReviewTask.item_a_id).in_(item_ids))
        .order_by(
            col(ReviewTask.item_a_id), col(ReviewTask.id), col(FactDecompReview.id)
        )
        .limit(MAX_ADMIN_EXPORT_REVIEW_ROWS + 1)
    ).all()
    if len(task_review_rows) > MAX_ADMIN_EXPORT_REVIEW_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"Export exceeds the {MAX_ADMIN_EXPORT_REVIEW_ROWS} review-row limit",
        )
    seen_task_ids: set[int] = set()
    for task, review in task_review_rows:
        if task.id is not None and task.id not in seen_task_ids:
            tasks_by_item[task.item_a_id].append(task)
            seen_task_ids.add(task.id)
        if review is not None:
            fact_reviews_by_task[review.task_id].append(review)
    task_counts = {item_id: len(tasks) for item_id, tasks in tasks_by_item.items()}
    reviews_by_item: defaultdict[int, list[RetrievalQAReview]] = defaultdict(list)
    for review in session.exec(
        select(RetrievalQAReview)
        .where(col(RetrievalQAReview.item_id).in_(item_ids))
        .order_by(col(RetrievalQAReview.item_id), col(RetrievalQAReview.id))
    ).all():
        reviews_by_item[review.item_id].append(review)

    rows: list[AdminExportItem] = []
    for item in items:
        assert item.id is not None
        chunks = [
            chunks_by_id[chunk_id]
            for chunk_id in sorted(chunk_ids_by_item[item.id])
            if chunk_id in chunks_by_id
        ]
        documents = [
            documents_by_id[document_id]
            for document_id in sorted({chunk.document_id for chunk in chunks})
            if document_id in documents_by_id
        ]
        if is_model_correction_item(item):
            try:
                metadata = read_correction_metadata(item)
            except CorrectionMetadataError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            if item.external_id is None:
                raise HTTPException(
                    status_code=500,
                    detail="Imported correction item has no external identity",
                )
            rows.append(
                AdminExportModelCorrectionItem(
                    review_mode="MODEL_LABEL_CORRECTION",
                    id=item.id,
                    dataset_id=item.dataset_id,
                    eval_type=EvalType.FACT_DECOMP,
                    status=item.status,
                    external_id=item.external_id,
                    case_id=metadata.case_id,
                    arm_id=metadata.arm_id,
                    canonical_row_sha256=metadata.canonical_row_sha256,
                    user_prompt=item.lazy_query,
                    assistant_response=item.prompt_text,
                    generator=metadata.generator.model_dump(mode="json"),
                    claims=_export_model_claims(item, facts_by_item[item.id]),
                    review_task=(
                        _export_review_task(tasks_by_item[item.id][0])
                        if tasks_by_item[item.id]
                        else None
                    ),
                    review_task_count=task_counts.get(item.id, 0),
                    correction_reviews=[
                        _export_correction_review(review)
                        for task in tasks_by_item[item.id]
                        for review in _reviews_for_task(fact_reviews_by_task, task)
                    ],
                )
            )
        else:
            rows.append(
                AdminExportAuthoredItem(
                    review_mode="AUTHORED",
                    id=item.id,
                    dataset_id=item.dataset_id,
                    eval_type=item.eval_type,
                    status=item.status,
                    prompt_text=item.prompt_text,
                    expected_answer=item.expected_answer,
                    category=item.category,
                    evidence_spans=item.evidence_spans or [],
                    evidence_chunks=[
                        {
                            "id": chunk.id,
                            "document_id": chunk.document_id,
                            "external_id": chunk.external_id,
                            "position": chunk.position,
                            "text": chunk.text,
                        }
                        for chunk in chunks
                    ],
                    evidence_documents=[
                        {
                            "id": document.id,
                            "external_id": document.external_id,
                            "title": document.title,
                        }
                        for document in documents
                    ],
                    facts=[
                        {
                            "fact_text": fact.fact_text,
                            "polarity": fact.polarity,
                            "position": fact.position,
                        }
                        for fact in facts_by_item[item.id]
                    ],
                    review_task_count=task_counts.get(item.id, 0),
                    retrieval_reviews=[
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
                        for review in reviews_by_item[item.id]
                    ],
                    fact_decomp_reviews=[
                        _export_fact_review(review)
                        for task in tasks_by_item[item.id]
                        for review in _reviews_for_task(fact_reviews_by_task, task)
                    ],
                )
            )
    return rows


def _reviews_for_task(
    reviews_by_task: defaultdict[int, list[FactDecompReview]],
    task: ReviewTask,
) -> list[FactDecompReview]:
    return reviews_by_task[task.id] if task.id is not None else []


def _export_model_claims(item: EvalItem, facts: list[EvalFact]) -> list[dict]:
    try:
        _metadata, claims = project_model_claims(item, facts)
    except CorrectionMetadataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        {
            "claim": claim.claim_text,
            "position": claim.position,
            "spans": [span.model_dump(mode="json") for span in claim.response_spans],
            "proposed_label": claim.proposed_label,
        }
        for claim in claims
    ]


def _export_review_task(task: ReviewTask) -> dict:
    return {
        "id": task.id,
        "is_active": task.is_active,
        "is_gold": task.is_gold,
        "labels_count": task.labels_count,
        "priority_score": task.priority_score,
    }


def _export_fact_review(review: FactDecompReview) -> dict:
    return {
        "id": review.id,
        "user_id": str(review.user_id),
        "item_revision": review.item_revision,
        "ratings": review.ratings or {},
        "reviewer_kind": review.reviewer_kind,
        "comment": review.comment,
        "flags": review.flags or {},
        "source": review.source,
    }


def _export_correction_review(review: FactDecompReview) -> dict:
    try:
        ratings = ModelCorrectionRating.model_validate(review.ratings or {})
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="Stored correction review is invalid"
        ) from exc
    return {
        "id": review.id,
        "user_id": str(review.user_id),
        "item_revision": review.item_revision,
        "proposed_labels": ratings.proposed_labels,
        "model_labels": ratings.model_labels,
        "human_claims": [
            claim.model_dump(mode="json") for claim in ratings.human_claims
        ],
        "reviewer_kind": review.reviewer_kind,
        "source": review.source,
    }


def _item_chunk_ids(item: EvalItem) -> set[int]:
    chunk_ids: set[int] = set()
    for value in [*(item.gold_chunk_ids or []), *(item.trap_chunk_ids or [])]:
        if isinstance(value, int):
            chunk_ids.add(value)
        elif isinstance(value, str) and value.isdigit():
            chunk_ids.add(int(value))
    for span in item.evidence_spans or []:
        if not isinstance(span, dict):
            continue
        value = span.get("chunk_id")
        if isinstance(value, int):
            chunk_ids.add(value)
        elif isinstance(value, str) and value.isdigit():
            chunk_ids.add(int(value))
    return chunk_ids
