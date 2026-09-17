from typing import Literal

from fastapi import HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Assignment,
    AssignmentMode,
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompReview,
    ItemStatus,
    PooledCandidate,
    RetrievalQAReview,
    ReviewTask,
)
from app.schemas import (
    AssignmentTerminalConflict,
    ChunkSummary,
    DocumentDetail,
    EvidenceSpan,
    NextReviewRecommendation,
    RetrievalReviewSubmission,
    ReviewDataset,
    ReviewItem,
    ReviewRubricDimension,
)
from app.services.assignment import (
    get_current_or_first_dataset_readonly,
    incomplete_loadable_assignments,
)
from app.services.fact_decomp_review import (
    CorrectionMetadataError,
    is_model_correction_item,
    project_model_claims,
)
from app.services.rubrics import FACT_DECOMP_DIMENSIONS


def read_existing_assignment_review(
    session: SessionDep,
    current_user: CurrentUser,
    *,
    mode: AssignmentMode,
) -> NextReviewRecommendation:
    assignments = incomplete_loadable_assignments(session, current_user, mode=mode)
    if not assignments:
        raise HTTPException(status_code=404, detail="No existing review assignment")
    return assignment_recommendation(session, assignments[0], created=False)


def assignment_recommendation(
    session: SessionDep,
    assignment: Assignment,
    *,
    created: bool,
) -> NextReviewRecommendation:
    assert assignment.id is not None
    if assignment.mode == AssignmentMode.RELEVANCE:
        candidate = session.get(PooledCandidate, assignment.target_id)
        candidate_item_id = candidate.item_id if candidate is not None else None
        title = "Review retrieved passage relevance"
        review_url = f"/review/relevance/{assignment.id}"
        kind: Literal["retrieval_audit", "fact_decomp", "relevance"] = "relevance"
    else:
        candidate_item_id = assignment.target_id
        title = "Review retrieval item"
        review_url = f"/review/retrieval/{assignment.id}"
        kind = "retrieval_audit"
    return NextReviewRecommendation(
        kind=kind,
        eval_type=EvalType.RETRIEVAL,
        dataset_id=assignment.dataset_id,
        title=title,
        reason="Next available assignment" if created else "Existing assignment",
        review_url=review_url,
        reservation_state="created" if created else "existing",
        assignment_id=assignment.id,
        item_id=candidate_item_id,
    )


def next_fact_decomp_review_readonly(
    session: SessionDep,
    current_user: CurrentUser,
) -> NextReviewRecommendation:
    tasks = available_fact_decomp_tasks(session, current_user)
    if not tasks:
        raise HTTPException(status_code=404, detail="No review tasks are available")
    dataset = session.get(Dataset, tasks[0].dataset_id)
    assert dataset is not None
    return fact_decomp_recommendation(session, dataset, tasks[0])


def available_fact_decomp_tasks(
    session: SessionDep, current_user: CurrentUser
) -> list[ReviewTask]:
    """Read eligible tasks across active fact datasets, preference first."""
    preferred = get_current_or_first_dataset_readonly(
        session, current_user, eval_type=EvalType.FACT_DECOMP
    )
    datasets = list(
        session.exec(
            select(Dataset)
            .where(
                col(Dataset.is_active) == True,  # noqa: E712
                col(Dataset.eval_type) == EvalType.FACT_DECOMP,
            )
            .order_by(col(Dataset.display_name), col(Dataset.id))
        ).all()
    )
    if preferred is not None:
        datasets.sort(key=lambda dataset: dataset.id != preferred.id)
    return [
        task
        for dataset in datasets
        for task in _eligible_fact_tasks(session, current_user, dataset)
    ]


def fact_decomp_recommendation(
    session: SessionDep,
    dataset: Dataset,
    task: ReviewTask,
) -> NextReviewRecommendation:
    item = session.get(EvalItem, task.item_a_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    assert dataset.id is not None
    assert task.id is not None
    assert item.id is not None
    return NextReviewRecommendation(
        kind="fact_decomp",
        eval_type=EvalType.FACT_DECOMP,
        dataset_id=dataset.id,
        title="Review fact decomposition",
        reason="Next fact-decomposition task",
        review_url=f"/review/fact-decomp/{task.id}",
        reservation_state="selected",
        task_id=task.id,
        item_id=item.id,
    )


def select_fact_decomp_task(
    session: SessionDep, current_user: CurrentUser, dataset: Dataset
) -> ReviewTask | None:
    tasks = _eligible_fact_tasks(session, current_user, dataset)
    return tasks[0] if tasks else None


def _eligible_fact_tasks(
    session: SessionDep, current_user: CurrentUser, dataset: Dataset
) -> list[ReviewTask]:
    tasks = session.exec(
        select(ReviewTask)
        .join(EvalItem, col(ReviewTask.item_a_id) == col(EvalItem.id))
        .where(
            col(ReviewTask.dataset_id) == dataset.id,
            col(ReviewTask.is_active) == True,  # noqa: E712
            col(EvalItem.dataset_id) == dataset.id,
            col(EvalItem.eval_type) == EvalType.FACT_DECOMP,
            col(EvalItem.status) == ItemStatus.ACTIVE,
            col(EvalItem.is_active) == True,  # noqa: E712
        )
        .order_by(
            col(ReviewTask.labels_count),
            col(ReviewTask.priority_score).desc(),
            col(ReviewTask.id),
        )
    ).all()
    eligible: list[ReviewTask] = []
    for task in tasks:
        item = session.get(EvalItem, task.item_a_id)
        if item is None or item.author_user_id == current_user.id:
            continue
        existing = session.exec(
            select(FactDecompReview).where(
                col(FactDecompReview.task_id) == task.id,
                col(FactDecompReview.user_id) == current_user.id,
            )
        ).first()
        if existing is not None:
            continue
        if is_model_correction_item(item):
            facts = list(
                session.exec(
                    select(EvalFact)
                    .where(col(EvalFact.item_id) == item.id)
                    .order_by(col(EvalFact.position))
                ).all()
            )
            try:
                project_model_claims(item, facts)
            except CorrectionMetadataError:
                continue
        eligible.append(task)
    return eligible


def read_assignment_for_user(
    session: SessionDep,
    assignment_id: int,
    current_user: CurrentUser,
    *,
    mode: AssignmentMode,
) -> Assignment:
    assignment = session.get(Assignment, assignment_id)
    if (
        assignment is None
        or assignment.user_id != current_user.id
        or assignment.mode != mode
    ):
        raise HTTPException(status_code=404, detail="Review assignment not found")
    if assignment.released_at is not None:
        raise_assignment_terminal_conflict(assignment_id)
    if assignment.completed_at is not None:
        raise_assignment_terminal_conflict(assignment_id)
    return assignment


def raise_assignment_terminal_conflict(assignment_id: int) -> None:
    detail = AssignmentTerminalConflict(
        message="Review assignment has already been completed or released.",
        assignment_id=assignment_id,
    )
    raise HTTPException(status_code=409, detail=detail.model_dump())


def read_active_review_task(session: SessionDep, task_id: int) -> ReviewTask:
    task = session.get(ReviewTask, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=404, detail="Review task not found")
    return task


def read_active_dataset(
    session: SessionDep, dataset_id: int, eval_type: EvalType
) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active or dataset.eval_type != eval_type:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


def read_active_item(
    session: SessionDep, item_id: int, eval_type: EvalType
) -> EvalItem:
    item = session.get(EvalItem, item_id)
    if (
        item is None
        or not item.is_active
        or item.status != ItemStatus.ACTIVE
        or item.eval_type != eval_type
    ):
        raise HTTPException(status_code=404, detail="Review item not found")
    return item


def dataset_payload(dataset: Dataset) -> ReviewDataset:
    assert dataset.id is not None
    return ReviewDataset(
        id=dataset.id,
        name=dataset.name,
        display_name=dataset.display_name,
        eval_type=dataset.eval_type,
    )


def item_payload(item: EvalItem) -> ReviewItem:
    assert item.id is not None
    return ReviewItem(
        id=item.id,
        dataset_id=item.dataset_id,
        eval_type=item.eval_type,
        prompt_text=item.prompt_text,
        status=item.status,
        revision=item.revision,
        category=item.category,
        expected_answer=item.expected_answer,
        why_not_answerable=item.why_not_answerable,
    )


def chunks_for_item(session: SessionDep, item: EvalItem) -> list[Chunk]:
    chunk_ids = {
        span.get("chunk_id")
        for span in item.evidence_spans or []
        if isinstance(span, dict) and span.get("chunk_id") is not None
    }
    chunk_ids.update(item.gold_chunk_ids or [])
    chunk_ids.update(item.trap_chunk_ids or [])
    if not chunk_ids and item.document_id is not None:
        return list(
            session.exec(
                select(Chunk)
                .where(col(Chunk.document_id) == item.document_id)
                .order_by(col(Chunk.position))
            ).all()
        )
    if not chunk_ids:
        return []
    return list(
        session.exec(
            select(Chunk)
            .where(
                col(Chunk.id).in_(chunk_ids), col(Chunk.dataset_id) == item.dataset_id
            )
            .order_by(col(Chunk.document_id), col(Chunk.position))
        ).all()
    )


def documents_for_chunks_and_item(
    session: SessionDep, item: EvalItem, chunks: list[Chunk]
) -> list[DocumentDetail]:
    document_ids = {chunk.document_id for chunk in chunks}
    if item.document_id is not None:
        document_ids.add(item.document_id)
    if not document_ids:
        return []
    documents = session.exec(
        select(Document)
        .where(
            col(Document.id).in_(document_ids),
            col(Document.dataset_id) == item.dataset_id,
            col(Document.is_active) == True,  # noqa: E712
        )
        .order_by(col(Document.title))
    ).all()
    return [document_detail(session, document) for document in documents]


def document_detail(session: SessionDep, document: Document) -> DocumentDetail:
    chunks = session.exec(
        select(Chunk)
        .where(col(Chunk.document_id) == document.id)
        .order_by(col(Chunk.position))
    ).all()
    assert document.id is not None
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


def retrieval_submission_payload(
    judgment: RetrievalQAReview,
) -> RetrievalReviewSubmission:
    assert judgment.id is not None
    return RetrievalReviewSubmission(
        id=judgment.id,
        question_validity=judgment.question_validity,
        evidence_quality=judgment.evidence_quality,
        answer_correctness=judgment.answer_correctness,
        answer_faithfulness=judgment.answer_faithfulness,
        notes=judgment.notes,
        verdict=judgment.verdict,
        skipped=judgment.skipped,
        skip_reason=judgment.skip_reason,
    )


def item_spans(item: EvalItem, *, kind: str) -> list[EvidenceSpan]:
    spans: list[EvidenceSpan] = []
    for row in item.evidence_spans or []:
        if not isinstance(row, dict):
            continue
        if row.get("kind", kind) != kind:
            continue
        try:
            spans.append(EvidenceSpan.model_validate(row))
        except ValueError:
            continue
    return spans


def fact_decomp_rubric() -> list[ReviewRubricDimension]:
    return [
        ReviewRubricDimension(
            key=dimension.id,
            label=dimension.label,
            options=[option for option, _label in dimension.options],
        )
        for dimension in FACT_DECOMP_DIMENSIONS
    ]
