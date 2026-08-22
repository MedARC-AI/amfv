from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.review_common import (
    assignment_recommendation,
    fact_decomp_recommendation,
    next_fact_decomp_review_readonly,
    raise_assignment_terminal_conflict,
    read_existing_assignment_review,
    select_fact_decomp_task,
)
from app.api.routes.review_fact_decomp import router as fact_decomp_router
from app.api.routes.review_relevance import router as relevance_router
from app.api.routes.review_retrieval import router as retrieval_router
from app.models import Assignment, AssignmentMode, EvalType, UserRole
from app.schemas import (
    AssignmentRelease,
    AssignmentReleaseResponse,
    AssignmentTerminalConflictResponse,
    NextReviewRecommendation,
    ReviewClaimRequest,
)
from app.services.assignment import (
    claim_assignment,
    get_or_create_current_dataset,
    release_assignment,
)

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
        return next_fact_decomp_review_readonly(session, current_user)
    return read_existing_assignment_review(session, current_user, mode=mode)


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
        task = select_fact_decomp_task(session, current_user, dataset)
        if task is None:
            raise HTTPException(status_code=404, detail="No review tasks are available")
        session.commit()
        return fact_decomp_recommendation(session, dataset, task)

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
    return assignment_recommendation(session, assignment, created=created)


@router.post(
    "/assignments/{assignment_id}/release",
    response_model=AssignmentReleaseResponse,
    responses={409: {"model": AssignmentTerminalConflictResponse}},
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
        raise HTTPException(
            status_code=403, detail="Cannot release another user's assignment"
        )
    assert assignment.id is not None
    assignment_identifier = assignment.id
    # Discard the read transaction before the conditional terminal write. SQLite
    # otherwise cannot promote a stale read snapshot after a competing writer.
    session.rollback()
    try:
        released = release_assignment(session, assignment, reason=body.reason)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not released:
        session.rollback()
        raise_assignment_terminal_conflict(assignment_identifier)
    session.commit()
    return AssignmentReleaseResponse(
        id=assignment_identifier,
        release_reason=body.reason.strip(),
    )


router.include_router(retrieval_router)
router.include_router(fact_decomp_router)
router.include_router(relevance_router)
