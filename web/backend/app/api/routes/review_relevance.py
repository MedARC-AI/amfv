from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.review_common import (
    dataset_payload,
    document_detail,
    item_payload,
    raise_assignment_terminal_conflict,
    read_active_dataset,
    read_active_item,
    read_assignment_for_user,
)
from app.models import (
    AssignmentMode,
    Chunk,
    Document,
    EvalType,
    PooledCandidate,
    RelevanceJudgment,
)
from app.schemas import (
    AssignmentTerminalConflictResponse,
    ChunkSummary,
    RelevanceReviewPayload,
    RelevanceReviewSubmit,
    ReviewSubmissionResponse,
)
from app.services.assignment import complete_assignment

router = APIRouter()


@router.get("/relevance/{assignment_id}", response_model=RelevanceReviewPayload)
def read_relevance_review(
    session: SessionDep,
    assignment_id: int,
    current_user: CurrentUser,
) -> RelevanceReviewPayload:
    assignment = read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.RELEVANCE
    )
    candidate = session.get(PooledCandidate, assignment.target_id)
    if candidate is None or candidate.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Relevance candidate not found")
    item = read_active_item(session, candidate.item_id, EvalType.RETRIEVAL)
    if item.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    dataset = read_active_dataset(session, assignment.dataset_id, EvalType.RETRIEVAL)
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
        dataset=dataset_payload(dataset),
        item=item_payload(item),
        assignment_id=assignment.id,
        candidate=candidate,
        document=document_detail(session, document),
        chunk=ChunkSummary.model_validate(chunk),
        allowed_actions=["grade_relevance"],
        item_revision=item.revision,
        existing_submission=existing.model_dump(mode="json") if existing else None,
    )


@router.post(
    "/relevance/{assignment_id}",
    response_model=ReviewSubmissionResponse,
    responses={409: {"model": AssignmentTerminalConflictResponse}},
)
def submit_relevance_review(
    session: SessionDep,
    assignment_id: int,
    body: RelevanceReviewSubmit,
    current_user: CurrentUser,
) -> ReviewSubmissionResponse:
    assignment = read_assignment_for_user(
        session, assignment_id, current_user, mode=AssignmentMode.RELEVANCE
    )
    candidate = session.get(PooledCandidate, assignment.target_id)
    if candidate is None or candidate.dataset_id != assignment.dataset_id:
        raise HTTPException(status_code=404, detail="Relevance candidate not found")
    item = read_active_item(session, candidate.item_id, EvalType.RETRIEVAL)
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
        select(RelevanceJudgment).where(
            col(RelevanceJudgment.assignment_id) == assignment.id
        )
    ).first():
        raise HTTPException(
            status_code=409, detail="Review assignment already submitted"
        )
    assert assignment.id is not None
    assert candidate.id is not None
    assert item.id is not None
    assert current_user.id is not None
    assignment_identifier = assignment.id
    candidate_identifier = candidate.id
    item_identifier = item.id
    user_id = current_user.id
    # Claim the terminal transition before the judgment enters this transaction.
    session.rollback()
    if not complete_assignment(session, assignment):
        session.rollback()
        raise_assignment_terminal_conflict(assignment_identifier)
    judgment = RelevanceJudgment(
        assignment_id=assignment_identifier,
        candidate_id=candidate_identifier,
        user_id=user_id,
        grade=body.grade,
        confidence=body.confidence,
    )
    session.add(judgment)
    session.commit()
    session.refresh(judgment)
    assert judgment.id is not None
    return ReviewSubmissionResponse(
        id=judgment.id,
        assignment_id=assignment_identifier,
        item_id=item_identifier,
        kind="relevance",
    )
