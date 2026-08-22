from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.review_common import (
    chunks_for_item,
    dataset_payload,
    documents_for_chunks_and_item,
    item_payload,
    item_spans,
    raise_assignment_terminal_conflict,
    read_active_dataset,
    read_active_item,
    read_assignment_for_user,
    retrieval_submission_payload,
)
from app.models import AssignmentMode, EvalType, ItemVerdict, RetrievalQAReview
from app.schemas import (
    AssignmentTerminalConflictResponse,
    ChunkSummary,
    RetrievalReviewPayload,
    RetrievalReviewSubmit,
    ReviewSubmissionResponse,
)
from app.services.assignment import complete_assignment

router = APIRouter()


@router.get("/retrieval/{assignment_id}", response_model=RetrievalReviewPayload)
def read_retrieval_review(
    session: SessionDep,
    assignment_id: int,
    current_user: CurrentUser,
) -> RetrievalReviewPayload:
    assignment = read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.ITEM_AUDIT
    )
    item = read_active_item(session, assignment.target_id, EvalType.RETRIEVAL)
    if item.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    dataset = read_active_dataset(session, assignment.dataset_id, EvalType.RETRIEVAL)
    existing = session.exec(
        select(RetrievalQAReview).where(
            col(RetrievalQAReview.assignment_id) == assignment.id
        )
    ).first()
    chunks = chunks_for_item(session, item)
    documents = documents_for_chunks_and_item(session, item, chunks)
    assert assignment.id is not None
    return RetrievalReviewPayload(
        dataset=dataset_payload(dataset),
        item=item_payload(item),
        assignment_id=assignment.id,
        documents=documents,
        chunks=[ChunkSummary.model_validate(chunk) for chunk in chunks],
        gold_evidence_spans=item_spans(item, kind="gold"),
        trap_evidence_spans=item_spans(item, kind="trap"),
        allowed_actions=["accept", "reject"],
        item_revision=item.revision,
        existing_submission=retrieval_submission_payload(existing)
        if existing
        else None,
    )


@router.post(
    "/retrieval/{assignment_id}",
    response_model=ReviewSubmissionResponse,
    responses={409: {"model": AssignmentTerminalConflictResponse}},
)
def submit_retrieval_review(
    session: SessionDep,
    assignment_id: int,
    body: RetrievalReviewSubmit,
    current_user: CurrentUser,
) -> ReviewSubmissionResponse:
    assignment = read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.ITEM_AUDIT
    )
    item = read_active_item(session, assignment.target_id, EvalType.RETRIEVAL)
    if item.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if session.exec(
        select(RetrievalQAReview).where(
            RetrievalQAReview.assignment_id == assignment.id
        )
    ).first():
        raise HTTPException(
            status_code=409, detail="Review assignment already submitted"
        )
    assert assignment.id is not None
    assert item.id is not None
    assert current_user.id is not None
    assignment_identifier = assignment.id
    item_identifier = item.id
    user_id = current_user.id
    # The conditional completion must start a fresh write transaction before a
    # judgment is added, so a losing concurrent request cannot persist one.
    session.rollback()
    if not complete_assignment(session, assignment):
        session.rollback()
        raise_assignment_terminal_conflict(assignment_identifier)
    judgment = RetrievalQAReview(
        assignment_id=assignment_identifier,
        item_id=item_identifier,
        user_id=user_id,
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
            else ItemVerdict.ACCEPT
            if body.accept_as_gold
            else ItemVerdict.REJECT
        ),
        skipped=body.skipped,
        skip_reason=body.skip_reason.strip() if body.skip_reason else None,
    )
    session.add(judgment)
    session.commit()
    session.refresh(judgment)
    assert judgment.id is not None
    return ReviewSubmissionResponse(
        id=judgment.id,
        assignment_id=assignment_identifier,
        item_id=item_identifier,
        kind="retrieval_audit",
    )
