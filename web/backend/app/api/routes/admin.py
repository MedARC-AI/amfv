import uuid
from itertools import combinations
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import func, select

from app.api.deps import (
    SessionDep,
    get_current_active_superuser,
    get_current_data_user,
)
from app.models import (
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
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
    DocumentSummary,
    NiceImportJobStatusResponse,
    NiceImportStart,
)
from app.services import nice_import, nice_import_scheduler
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


@router.get("/datasets", response_model=list[DatasetSummary])
def read_admin_datasets(session: SessionDep) -> Any:
    datasets = session.exec(select(Dataset).order_by(Dataset.display_name)).all()
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


@router.post("/datasets/{dataset_id}/generate-tasks", response_model=AdminTaskGenerationResult)
def generate_dataset_tasks(session: SessionDep, dataset_id: int) -> Any:
    dataset = _get_dataset_or_404(session, dataset_id)
    item_ids = session.exec(
        select(EvalItem.id).where(
            EvalItem.dataset_id == dataset.id,
            EvalItem.status == ItemStatus.ACTIVE,
            EvalItem.is_active == True,  # noqa: E712
        )
    ).all()
    existing_item_ids = set(
        session.exec(
            select(ReviewTask.item_a_id).where(ReviewTask.dataset_id == dataset.id)
        ).all()
    )
    created = 0
    for item_id in item_ids:
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
    documents = session.exec(select(Document).order_by(Document.title)).all()
    return [DocumentSummary.model_validate(document) for document in documents]


@router.post("/documents", response_model=DocumentDetail)
def create_admin_document(session: SessionDep, body: DocumentCreate) -> Any:
    dataset = _get_dataset_or_404(session, body.dataset_id)
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


@router.post("/nice-imports", response_model=NiceImportJobStatusResponse)
def start_nice_import(
    session: SessionDep,
    body: NiceImportStart,
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    try:
        job = nice_import.create_import_job(
            session, limit=body.limit, started_by=current_user
        )
    except nice_import.NiceImportAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    nice_import_scheduler.ensure_started()
    return _nice_import_status(job)


@router.get("/nice-imports/current", response_model=NiceImportJobStatusResponse | None)
def read_current_nice_import(
    session: SessionDep,
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    _ = current_user
    job = nice_import.get_current_or_recent_job(session)
    if job is not None and job.active_slot == 1:
        nice_import_scheduler.nudge_if_unfinished(session)
    return _nice_import_status(job) if job is not None else None


@router.post(
    "/nice-imports/{job_id}/cancel",
    response_model=NiceImportJobStatusResponse,
)
def cancel_nice_import(
    session: SessionDep,
    job_id: int,
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    _ = current_user
    try:
        job = nice_import.cancel_job(session, job_id=job_id)
    except nice_import.NiceImportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _nice_import_status(job)


@router.get("/items", response_model=list[AdminItemSummary])
def read_admin_items(
    session: SessionDep,
    dataset_id: int | None = None,
    status: ItemStatus | None = ItemStatus.SUBMITTED,
) -> Any:
    statement = select(EvalItem).order_by(EvalItem.created_at.desc())
    if dataset_id is not None:
        statement = statement.where(EvalItem.dataset_id == dataset_id)
    if status is not None:
        statement = statement.where(EvalItem.status == status)
    items = session.exec(statement).all()
    return [_admin_item_summary(item) for item in items]


@router.post("/items/{item_id}/approve", response_model=AdminItemSummary)
def approve_admin_item(session: SessionDep, item_id: int, body: AdminModerationAction) -> Any:
    item = _moderate_item(session, item_id, body, ItemStatus.ACTIVE)
    return _admin_item_summary(item)


@router.post("/items/{item_id}/reject", response_model=AdminItemSummary)
def reject_admin_item(session: SessionDep, item_id: int, body: AdminModerationAction) -> Any:
    status = ItemStatus.DRAFT if body.action == "return_to_draft" else ItemStatus.REJECTED
    item = _moderate_item(session, item_id, body, status)
    return _admin_item_summary(item)


@router.get("/users")
def read_admin_users() -> Any:
    raise HTTPException(status_code=501, detail="Admin users API is not implemented yet")


@router.patch("/users/{user_id}")
def update_admin_user(user_id: uuid.UUID) -> Any:
    _ = user_id
    raise HTTPException(status_code=501, detail="Admin user updates are not implemented yet")


@router.get("/users/{user_id}/reviews")
def read_admin_user_reviews(user_id: uuid.UUID) -> Any:
    _ = user_id
    raise HTTPException(status_code=501, detail="User review history is not implemented yet")


@router.get("/metrics/agreement", response_model=list[AdminAgreementMetric])
def read_agreement_metrics(
    session: SessionDep,
    dataset_id: int | None = None,
    reviewer_kind: ReviewerKind | None = None,
) -> Any:
    if dataset_id is None:
        datasets = session.exec(select(Dataset.id).order_by(Dataset.id)).all()
        summaries = [
            summary
            for current_dataset_id in datasets
            for summary in dataset_agreement(
                session, current_dataset_id, reviewer_kind=reviewer_kind
            )
        ]
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
    users = session.exec(select(User).order_by(User.email)).all()
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
            {
                user_id
                for labels in judgments.values()
                for user_id in labels
            },
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
    statement = select(EvalItem).order_by(EvalItem.dataset_id, EvalItem.id)
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
        statement = statement.where(EvalItem.dataset_id == dataset_id)
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


def _document_detail(session: SessionDep, document: Document) -> DocumentDetail:
    chunks = session.exec(
        select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.position)
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


def _nice_import_status(job) -> NiceImportJobStatusResponse:
    return NiceImportJobStatusResponse(
        id=job.id or 0,
        status=job.status,
        requested_limit=job.requested_limit,
        target_count=job.target_count,
        completed_count=job.completed_count,
        failed_count=job.failed_count,
        started_by_user_id=str(job.started_by_user_id),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        last_error=job.last_error,
        heartbeat_at=job.heartbeat_at.isoformat() if job.heartbeat_at else None,
    )


def _admin_item_summary(item: EvalItem) -> AdminItemSummary:
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
    item.rejection_reason = body.reason if status in {ItemStatus.REJECTED, ItemStatus.DRAFT} else None
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _export_item(session: SessionDep, item: EvalItem) -> dict:
    facts = session.exec(
        select(EvalFact).where(EvalFact.item_id == item.id).order_by(EvalFact.position)
    ).all()
    chunks = resolve_item_chunks(session, item)
    documents = documents_for_chunks(session, chunks)
    review_task_count = session.exec(
        select(func.count(ReviewTask.id)).where(ReviewTask.item_a_id == item.id)
    ).one()
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
    }


def _user_metric(
    session: SessionDep,
    user: User,
    *,
    dataset_id: int | None,
) -> AdminUserMetric:
    authored_statement = select(func.count(EvalItem.id)).where(
        EvalItem.author_user_id == user.id
    )
    fact_decomp_review_statement = select(func.count(FactDecompReview.id)).where(
        FactDecompReview.user_id == user.id
    )
    retrieval_qa_review_statement = select(func.count(RetrievalQAReview.id)).where(
        RetrievalQAReview.user_id == user.id
    )
    relevance_statement = select(func.count(RelevanceJudgment.id)).where(
        RelevanceJudgment.user_id == user.id
    )
    if dataset_id is not None:
        _get_dataset_or_404(session, dataset_id)
        authored_statement = authored_statement.where(EvalItem.dataset_id == dataset_id)
        fact_decomp_review_statement = fact_decomp_review_statement.join(
            ReviewTask, FactDecompReview.task_id == ReviewTask.id
        ).where(ReviewTask.dataset_id == dataset_id)
        retrieval_qa_review_statement = retrieval_qa_review_statement.join(
            EvalItem, RetrievalQAReview.item_id == EvalItem.id
        ).where(EvalItem.dataset_id == dataset_id)
        relevance_statement = relevance_statement.join(
            PooledCandidate, RelevanceJudgment.candidate_id == PooledCandidate.id
        ).where(PooledCandidate.dataset_id == dataset_id)
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
