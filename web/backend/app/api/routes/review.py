from typing import Literal

from fastapi import APIRouter, HTTPException, Query
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
    ItemVerdict,
    PooledCandidate,
    RelevanceJudgment,
    RetrievalQAReview,
    ReviewTask,
    UserRole,
)
from app.schemas import (
    AssignmentRelease,
    AssignmentReleaseResponse,
    ChunkSummary,
    DocumentDetail,
    EvidenceSpan,
    FactDecompReviewPayload,
    FactDecompReviewSubmissionResponse,
    FactDecompReviewSubmit,
    NextReviewRecommendation,
    RelevanceReviewPayload,
    RelevanceReviewSubmit,
    RetrievalReviewPayload,
    RetrievalReviewSubmission,
    RetrievalReviewSubmit,
    ReviewClaimRequest,
    ReviewDataset,
    ReviewFact,
    ReviewItem,
    ReviewRubricDimension,
    ReviewSubmissionResponse,
)
from app.services.assignment import (
    claim_assignment,
    complete_assignment,
    get_current_or_first_dataset_readonly,
    get_or_create_current_dataset,
    incomplete_loadable_assignments,
    release_assignment,
)
from app.services.rubrics import FACT_DECOMP_DIMENSIONS, validate_fact_decomp_ratings

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/next", response_model=NextReviewRecommendation)
def read_next_review_task(
    session: SessionDep,
    current_user: CurrentUser,
    eval_type: EvalType,
    mode: AssignmentMode = Query(default=AssignmentMode.ITEM_AUDIT),
) -> NextReviewRecommendation:
    """Read an existing assignment or fact task without creating a claim."""

    if eval_type == EvalType.FACT_DECOMP:
        if mode != AssignmentMode.ITEM_AUDIT:
            raise HTTPException(
                status_code=400,
                detail="Fact-decomposition review does not use assignment mode",
            )
        return _next_fact_decomp_review_readonly(session, current_user)
    return _read_existing_assignment_review(session, current_user, mode=mode)


@router.post("/claim", response_model=NextReviewRecommendation)
def claim_next_review_task(
    session: SessionDep,
    body: ReviewClaimRequest,
    current_user: CurrentUser,
) -> NextReviewRecommendation:
    """Claim the next eligible review slot through a POST-only mutation."""

    if body.eval_type == EvalType.FACT_DECOMP:
        if body.mode != AssignmentMode.ITEM_AUDIT:
            raise HTTPException(
                status_code=400,
                detail="Fact-decomposition review does not use assignment mode",
            )
        dataset = get_or_create_current_dataset(
            session,
            current_user,
            eval_type=EvalType.FACT_DECOMP,
            dataset_id=body.dataset_id,
        )
        if dataset is None:
            raise HTTPException(status_code=404, detail="No review tasks are available")
        task = _select_fact_decomp_task(session, current_user, dataset)
        if task is None:
            raise HTTPException(status_code=404, detail="No review tasks are available")
        session.commit()
        return _fact_decomp_recommendation(session, dataset, task)

    assignment, created = claim_assignment(
        session,
        current_user,
        body.mode,
        eval_type=EvalType.RETRIEVAL,
        dataset_id=body.dataset_id,
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="No review tasks are available")
    session.commit()
    session.refresh(assignment)
    return _assignment_recommendation(session, assignment, created=created)


@router.post(
    "/assignments/{assignment_id}/release",
    response_model=AssignmentReleaseResponse,
)
def release_review_assignment(
    session: SessionDep,
    assignment_id: int,
    body: AssignmentRelease,
    current_user: CurrentUser,
) -> AssignmentReleaseResponse:
    """Explicitly return one incomplete assignment slot to the queue."""

    assignment = session.get(Assignment, assignment_id)
    is_admin = current_user.is_superuser or current_user.role == UserRole.admin
    if assignment is None:
        raise HTTPException(status_code=404, detail="Review assignment not found")
    if assignment.user_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Cannot release another user's assignment")
    try:
        release_assignment(session, assignment, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    assert assignment.id is not None
    assert assignment.release_reason is not None
    return AssignmentReleaseResponse(
        id=assignment.id,
        release_reason=assignment.release_reason,
    )


@router.get("/retrieval/{assignment_id}", response_model=RetrievalReviewPayload)
def read_retrieval_review(
    session: SessionDep,
    assignment_id: int,
    current_user: CurrentUser,
) -> RetrievalReviewPayload:
    assignment = _read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.ITEM_AUDIT
    )
    item = _read_active_item(session, assignment.target_id, EvalType.RETRIEVAL)
    if item.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    dataset = _read_active_dataset(session, assignment.dataset_id, EvalType.RETRIEVAL)
    existing = session.exec(
        select(RetrievalQAReview).where(col(RetrievalQAReview.assignment_id) == assignment.id)
    ).first()
    chunks = _chunks_for_item(session, item)
    documents = _documents_for_chunks_and_item(session, item, chunks)
    assert assignment.id is not None
    return RetrievalReviewPayload(
        dataset=_dataset_payload(dataset),
        item=_item_payload(item),
        assignment_id=assignment.id,
        documents=documents,
        chunks=[ChunkSummary.model_validate(chunk) for chunk in chunks],
        gold_evidence_spans=_item_spans(item, kind="gold"),
        trap_evidence_spans=_item_spans(item, kind="trap"),
        allowed_actions=["accept", "reject"],
        item_revision=item.revision,
        existing_submission=_retrieval_submission_payload(existing) if existing else None,
    )


@router.post("/retrieval/{assignment_id}", response_model=ReviewSubmissionResponse)
def submit_retrieval_review(
    session: SessionDep,
    assignment_id: int,
    body: RetrievalReviewSubmit,
    current_user: CurrentUser,
) -> ReviewSubmissionResponse:
    assignment = _read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.ITEM_AUDIT
    )
    item = _read_active_item(session, assignment.target_id, EvalType.RETRIEVAL)
    if item.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if session.exec(
        select(RetrievalQAReview).where(RetrievalQAReview.assignment_id == assignment.id)
    ).first():
        raise HTTPException(status_code=409, detail="Review assignment already submitted")
    assert assignment.id is not None
    assert item.id is not None
    judgment = RetrievalQAReview(
        assignment_id=assignment.id,
        item_id=item.id,
        user_id=current_user.id,
        question_validity=body.question_validity,
        evidence_quality=body.evidence_quality,
        answer_correctness=body.answer_correctness,
        answer_faithfulness=body.answer_faithfulness,
        notes=body.notes,
        span=None,
        confidence=None,
        verdict=(
            None
            if body.skipped
            else ItemVerdict.ACCEPT if body.accept_as_gold else ItemVerdict.REJECT
        ),
        skipped=body.skipped,
        skip_reason=body.skip_reason.strip() if body.skip_reason else None,
    )
    session.add(judgment)
    complete_assignment(session, assignment)
    session.commit()
    session.refresh(judgment)
    assert assignment.id is not None
    assert item.id is not None
    assert judgment.id is not None
    return ReviewSubmissionResponse(
        id=judgment.id,
        assignment_id=assignment.id,
        item_id=item.id,
        kind="retrieval_audit",
    )


@router.get("/fact-decomp/{task_id}", response_model=FactDecompReviewPayload)
def read_fact_decomp_review(
    session: SessionDep,
    task_id: int,
    current_user: CurrentUser,
) -> FactDecompReviewPayload:
    task = _read_active_review_task(session, task_id)
    item = _read_active_item(session, task.item_a_id, EvalType.FACT_DECOMP)
    if item.dataset_id != task.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if item.author_user_id == current_user.id:
        raise HTTPException(status_code=403, detail="Cannot review your own item")
    dataset = _read_active_dataset(session, task.dataset_id, EvalType.FACT_DECOMP)
    existing = session.exec(
        select(FactDecompReview).where(col(FactDecompReview.task_id) == task.id, col(FactDecompReview.user_id) == current_user.id)
    ).first()
    chunks = _chunks_for_item(session, item)
    documents = _documents_for_chunks_and_item(session, item, chunks)
    facts = session.exec(
        select(EvalFact).where(col(EvalFact.item_id) == item.id).order_by(col(EvalFact.position))
    ).all()
    assert task.id is not None
    return FactDecompReviewPayload(
        dataset=_dataset_payload(dataset),
        item=_item_payload(item),
        task_id=task.id,
        facts=[ReviewFact.model_validate(fact) for fact in facts],
        rubric_dimensions=_fact_decomp_rubric(),
        documents=documents,
        chunks=[ChunkSummary.model_validate(chunk) for chunk in chunks],
        allowed_actions=["save_review"],
        item_revision=item.revision,
        existing_review=existing.model_dump(mode="json") if existing else None,
    )


@router.post("/fact-decomp/{task_id}", response_model=FactDecompReviewSubmissionResponse)
def submit_fact_decomp_review(
    session: SessionDep,
    task_id: int,
    body: FactDecompReviewSubmit,
    current_user: CurrentUser,
) -> FactDecompReviewSubmissionResponse:
    task = _read_active_review_task(session, task_id)
    item = _read_active_item(session, task.item_a_id, EvalType.FACT_DECOMP)
    if item.dataset_id != task.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if item.author_user_id == current_user.id:
        raise HTTPException(status_code=403, detail="Cannot review your own item")
    _read_active_dataset(session, task.dataset_id, EvalType.FACT_DECOMP)
    if body.item_revision != item.revision:
        raise HTTPException(status_code=409, detail="Item revision is stale")
    if session.exec(
        select(FactDecompReview).where(col(FactDecompReview.task_id) == task.id, col(FactDecompReview.user_id) == current_user.id)
    ).first():
        raise HTTPException(status_code=409, detail="Review task already submitted")
    facts = session.exec(
        select(EvalFact).where(col(EvalFact.item_id) == item.id).order_by(col(EvalFact.position))
    ).all()
    try:
        ratings = validate_fact_decomp_ratings(
            item,
            list(facts),
            fact_calls=body.fact_calls,
            values=body.values,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=[{"level": "error", "message": str(exc)}],
        ) from exc
    assert task.id is not None
    review = FactDecompReview(
        task_id=task.id,
        user_id=current_user.id,
        item_revision=item.revision,
        ratings=ratings,
        reviewer_kind=current_user.reviewer_kind,
        comment=body.comments,
        flags={"confidence": body.confidence.value if body.confidence else None},
        source="web",
    )
    task.labels_count += 1
    session.add(review)
    session.add(task)
    session.commit()
    session.refresh(review)
    session.refresh(task)
    assert task.id is not None
    assert item.id is not None
    assert review.id is not None
    return FactDecompReviewSubmissionResponse(
        id=review.id,
        task_id=task.id,
        item_id=item.id,
        labels_count=task.labels_count,
    )


@router.get("/relevance/{assignment_id}", response_model=RelevanceReviewPayload)
def read_relevance_review(
    session: SessionDep,
    assignment_id: int,
    current_user: CurrentUser,
) -> RelevanceReviewPayload:
    assignment = _read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.RELEVANCE
    )
    candidate = session.get(PooledCandidate, assignment.target_id)
    if candidate is None or candidate.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Relevance candidate not found")
    item = _read_active_item(session, candidate.item_id, EvalType.RETRIEVAL)
    if item.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    dataset = _read_active_dataset(session, assignment.dataset_id, EvalType.RETRIEVAL)
    chunk = session.get(Chunk, candidate.chunk_id)
    if chunk is None or chunk.dataset_id != dataset.id:
        raise HTTPException(status_code=404, detail="Candidate chunk not found")
    document = session.get(Document, chunk.document_id)
    if (
        document is None
        or not document.is_active
        or document.id != chunk.document_id
        or document.dataset_id != assignment.dataset_id
    ):
        raise HTTPException(status_code=404, detail="Candidate document not found")
    existing = session.exec(
        select(RelevanceJudgment).where(
            col(RelevanceJudgment.assignment_id) == assignment.id
        )
    ).first()
    assert assignment.id is not None
    assert candidate.id is not None
    return RelevanceReviewPayload(
        dataset=_dataset_payload(dataset),
        item=_item_payload(item),
        assignment_id=assignment.id,
        candidate=candidate,
        document=_document_detail(session, document),
        chunk=ChunkSummary.model_validate(chunk),
        allowed_actions=["grade_relevance"],
        item_revision=item.revision,
        existing_submission=existing.model_dump(mode="json") if existing else None,
    )


@router.post("/relevance/{assignment_id}", response_model=ReviewSubmissionResponse)
def submit_relevance_review(
    session: SessionDep,
    assignment_id: int,
    body: RelevanceReviewSubmit,
    current_user: CurrentUser,
) -> ReviewSubmissionResponse:
    assignment = _read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.RELEVANCE
    )
    candidate = session.get(PooledCandidate, assignment.target_id)
    if candidate is None or candidate.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Relevance candidate not found")
    item = _read_active_item(session, candidate.item_id, EvalType.RETRIEVAL)
    if item.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if body.item_revision != item.revision:
        raise HTTPException(status_code=409, detail="Item revision is stale")
    chunk = session.get(Chunk, candidate.chunk_id)
    if chunk is None or chunk.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Candidate chunk not found")
    document = session.get(Document, chunk.document_id)
    if (
        document is None
        or not document.is_active
        or document.id != chunk.document_id
        or document.dataset_id != assignment.dataset_id
    ):
        raise HTTPException(status_code=404, detail="Candidate document not found")
    if session.exec(
        select(RelevanceJudgment).where(col(RelevanceJudgment.assignment_id) == assignment.id)
    ).first():
        raise HTTPException(status_code=409, detail="Review assignment already submitted")
    assert assignment.id is not None
    assert candidate.id is not None
    judgment = RelevanceJudgment(
        assignment_id=assignment.id,
        candidate_id=candidate.id,
        user_id=current_user.id,
        grade=body.grade,
        confidence=body.confidence,
    )
    session.add(judgment)
    complete_assignment(session, assignment)
    session.commit()
    session.refresh(judgment)
    assert assignment.id is not None
    assert candidate.id is not None
    assert item.id is not None
    assert judgment.id is not None
    return ReviewSubmissionResponse(
        id=judgment.id,
        assignment_id=assignment.id,
        item_id=item.id,
        kind="relevance",
    )


def _read_existing_assignment_review(
    session: SessionDep,
    current_user: CurrentUser,
    *,
    mode: AssignmentMode,
) -> NextReviewRecommendation:
    assignments = incomplete_loadable_assignments(session, current_user, mode=mode)
    if not assignments:
        raise HTTPException(status_code=404, detail="No existing review assignment")
    return _assignment_recommendation(session, assignments[0], created=False)


def _assignment_recommendation(
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


def _next_fact_decomp_review_readonly(
    session: SessionDep,
    current_user: CurrentUser,
) -> NextReviewRecommendation:
    dataset = get_current_or_first_dataset_readonly(
        session,
        current_user,
        eval_type=EvalType.FACT_DECOMP,
    )
    if dataset is None:
        raise HTTPException(status_code=404, detail="No review tasks are available")
    task = _select_fact_decomp_task(session, current_user, dataset)
    if task is None:
        raise HTTPException(status_code=404, detail="No review tasks are available")
    return _fact_decomp_recommendation(session, dataset, task)


def _fact_decomp_recommendation(
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


def _select_fact_decomp_task(
    session: SessionDep, current_user: CurrentUser, dataset: Dataset
) -> ReviewTask | None:
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
        .order_by(col(ReviewTask.labels_count), col(ReviewTask.priority_score).desc(), col(ReviewTask.id))
    ).all()
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
        return task
    return None


def _read_assignment_for_user(
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
        raise HTTPException(status_code=409, detail="Review assignment has been released")
    if assignment.completed_at is not None:
        raise HTTPException(status_code=409, detail="Review assignment is complete")
    return assignment


def _read_active_review_task(session: SessionDep, task_id: int) -> ReviewTask:
    task = session.get(ReviewTask, task_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=404, detail="Review task not found")
    return task


def _read_active_dataset(
    session: SessionDep, dataset_id: int, eval_type: EvalType
) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active or dataset.eval_type != eval_type:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


def _read_active_item(session: SessionDep, item_id: int, eval_type: EvalType) -> EvalItem:
    item = session.get(EvalItem, item_id)
    if (
        item is None
        or not item.is_active
        or item.status != ItemStatus.ACTIVE
        or item.eval_type != eval_type
    ):
        raise HTTPException(status_code=404, detail="Review item not found")
    return item


def _dataset_payload(dataset: Dataset) -> ReviewDataset:
    assert dataset.id is not None
    return ReviewDataset(
        id=dataset.id,
        name=dataset.name,
        display_name=dataset.display_name,
        eval_type=dataset.eval_type,
    )


def _item_payload(item: EvalItem) -> ReviewItem:
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


def _chunks_for_item(session: SessionDep, item: EvalItem) -> list[Chunk]:
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
            .where(col(Chunk.id).in_(chunk_ids), col(Chunk.dataset_id) == item.dataset_id)
            .order_by(col(Chunk.document_id), col(Chunk.position))
        ).all()
    )


def _documents_for_chunks_and_item(
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
    return [_document_detail(session, document) for document in documents]


def _document_detail(session: SessionDep, document: Document) -> DocumentDetail:
    chunks = session.exec(
        select(Chunk).where(col(Chunk.document_id) == document.id).order_by(col(Chunk.position))
    ).all()
    assert document.id is not None
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


def _retrieval_submission_payload(
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


def _item_spans(item: EvalItem, *, kind: str) -> list[EvidenceSpan]:
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


def _fact_decomp_rubric() -> list[ReviewRubricDimension]:
    return [
        ReviewRubricDimension(
            key=dimension.id,
            label=dimension.label,
            options=[option for option, _label in dimension.options],
        )
        for dimension in FACT_DECOMP_DIMENSIONS
    ]
