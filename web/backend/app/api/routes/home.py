from typing import Any

from fastapi import APIRouter
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Assignment,
    AssignmentMode,
    EvalItem,
    EvalType,
    FactDecompReview,
    ItemStatus,
    PooledCandidate,
    RelevanceJudgment,
    RetrievalQAReview,
    ReviewTask,
)
from app.schemas import HomeSummary, RecommendedTask, UserPublic
from app.services.assignment import (
    available_item_audit_targets,
    available_relevance_candidates,
    get_current_or_first_dataset_readonly,
    incomplete_loadable_assignments,
)

router = APIRouter(prefix="/home", tags=["home"])

COUNT_KEYS = (
    "retrieval_reviews",
    "fact_decomp_reviews",
    "relevance_reviews",
    "draft_items",
    "submitted_items",
    "authored_items",
)


@router.get("/summary", response_model=HomeSummary)
def read_home_summary(session: SessionDep, current_user: CurrentUser) -> Any:
    outstanding_counts = dict.fromkeys(COUNT_KEYS, 0)
    outstanding_counts["retrieval_reviews"] = _retrieval_review_count(
        session, current_user
    )
    outstanding_counts["fact_decomp_reviews"] = _fact_decomp_review_count(
        session, current_user
    )
    outstanding_counts["relevance_reviews"] = _relevance_review_count(
        session, current_user
    )
    outstanding_counts["draft_items"] = _authored_item_count(
        session, current_user, status=ItemStatus.DRAFT
    )
    outstanding_counts["submitted_items"] = _authored_item_count(
        session, current_user, status=ItemStatus.SUBMITTED
    )
    outstanding_counts["authored_items"] = _authored_item_count(session, current_user)

    reviewed_total = (
        _row_count(session, col(RetrievalQAReview.user_id) == current_user.id)
        + _row_count(session, col(RelevanceJudgment.user_id) == current_user.id)
        + _row_count(session, col(FactDecompReview.user_id) == current_user.id)
    )

    return HomeSummary(
        user=UserPublic.model_validate(current_user),
        outstanding_counts=outstanding_counts,
        authored_total=outstanding_counts["authored_items"],
        reviewed_total=reviewed_total,
        recommended_task=_recommended_task(session, current_user),
    )


def _row_count(session: SessionDep, *where: Any) -> int:
    statement = select(func.count()).where(*where)
    return session.exec(statement).one()


def _authored_item_count(
    session: SessionDep, current_user: CurrentUser, *, status: ItemStatus | None = None
) -> int:
    where = [
        col(EvalItem.author_user_id) == current_user.id,
        col(EvalItem.is_active) == True,  # noqa: E712
    ]
    if status is not None:
        where.append(col(EvalItem.status) == status)
    return _row_count(session, *where)


def _recommended_task(
    session: SessionDep, current_user: CurrentUser
) -> RecommendedTask | None:
    existing = _first_loadable_incomplete_assignment(session, current_user)
    if existing is not None:
        if existing.mode == AssignmentMode.RELEVANCE:
            return RecommendedTask(
                kind="relevance",
                eval_type=EvalType.RETRIEVAL,
                dataset_id=existing.dataset_id,
                title="Continue relevance review",
                reason="Existing assignment",
                assignment_id=existing.id,
                task_id=None,
            )
        return RecommendedTask(
            kind="retrieval_audit",
            eval_type=EvalType.RETRIEVAL,
            dataset_id=existing.dataset_id,
            title="Continue retrieval review",
            reason="Existing assignment",
            assignment_id=existing.id,
        )

    retrieval_item = _first_available_retrieval_item(session, current_user)
    if retrieval_item is not None:
        return RecommendedTask(
            kind="retrieval_audit",
            eval_type=EvalType.RETRIEVAL,
            dataset_id=retrieval_item.dataset_id,
            title="Review retrieval item",
            reason="Next available retrieval review",
        )

    fact_task = _first_available_fact_decomp_task(session, current_user)
    if fact_task is not None:
        return RecommendedTask(
            kind="fact_decomp",
            eval_type=EvalType.FACT_DECOMP,
            dataset_id=fact_task.dataset_id,
            title="Review fact decomposition",
            reason="Next available fact-decomposition review",
            task_id=fact_task.id,
        )

    relevance_candidate = _first_available_relevance_candidate(session, current_user)
    if relevance_candidate is not None:
        return RecommendedTask(
            kind="relevance",
            eval_type=EvalType.RETRIEVAL,
            dataset_id=relevance_candidate.dataset_id,
            title="Review retrieved passage relevance",
            reason="Next available relevance review",
        )

    return None


def _retrieval_review_count(session: SessionDep, current_user: CurrentUser) -> int:
    existing = _valid_incomplete_assignments(
        session, current_user, mode=AssignmentMode.ITEM_AUDIT
    )
    available = _available_retrieval_items(session, current_user)
    return len(existing) + len(available)


def _fact_decomp_review_count(session: SessionDep, current_user: CurrentUser) -> int:
    return len(_available_fact_decomp_tasks(session, current_user))


def _relevance_review_count(session: SessionDep, current_user: CurrentUser) -> int:
    existing = _valid_incomplete_assignments(
        session, current_user, mode=AssignmentMode.RELEVANCE
    )
    available = _available_relevance_candidates(session, current_user)
    return len(existing) + len(available)


def _first_available_retrieval_item(
    session: SessionDep, current_user: CurrentUser
) -> EvalItem | None:
    items = _available_retrieval_items(session, current_user)
    return items[0] if items else None


def _available_retrieval_items(
    session: SessionDep, current_user: CurrentUser
) -> list[EvalItem]:
    dataset = get_current_or_first_dataset_readonly(
        session, current_user, eval_type=EvalType.RETRIEVAL
    )
    if dataset is None:
        return []
    return available_item_audit_targets(session, current_user, dataset)


def _first_available_fact_decomp_task(
    session: SessionDep, current_user: CurrentUser
) -> ReviewTask | None:
    tasks = _available_fact_decomp_tasks(session, current_user)
    return tasks[0] if tasks else None


def _available_fact_decomp_tasks(
    session: SessionDep, current_user: CurrentUser
) -> list[ReviewTask]:
    dataset = get_current_or_first_dataset_readonly(
        session, current_user, eval_type=EvalType.FACT_DECOMP
    )
    if dataset is None:
        return []
    tasks = session.exec(
        select(ReviewTask)
        .join(EvalItem, col(ReviewTask.item_a_id) == col(EvalItem.id))
        .where(
            col(ReviewTask.dataset_id) == dataset.id,
            col(ReviewTask.is_active) == True,  # noqa: E712
            col(EvalItem.dataset_id) == col(ReviewTask.dataset_id),
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
    available: list[ReviewTask] = []
    for task in tasks:
        item = session.get(EvalItem, task.item_a_id)
        if item is None or item.author_user_id == current_user.id:
            continue
        if session.exec(
            select(FactDecompReview).where(
                col(FactDecompReview.task_id) == task.id,
                col(FactDecompReview.user_id) == current_user.id,
            )
        ).first():
            continue
        available.append(task)
    return available


def _first_available_relevance_candidate(
    session: SessionDep, current_user: CurrentUser
) -> PooledCandidate | None:
    candidates = _available_relevance_candidates(session, current_user)
    return candidates[0] if candidates else None


def _available_relevance_candidates(
    session: SessionDep, current_user: CurrentUser
) -> list[PooledCandidate]:
    dataset = get_current_or_first_dataset_readonly(
        session, current_user, eval_type=EvalType.RETRIEVAL
    )
    if dataset is None:
        return []
    return available_relevance_candidates(session, current_user, dataset)


def _valid_incomplete_assignments(
    session: SessionDep, current_user: CurrentUser, *, mode: AssignmentMode
) -> list[Assignment]:
    return incomplete_loadable_assignments(session, current_user, mode=mode)


def _first_loadable_incomplete_assignment(
    session: SessionDep, current_user: CurrentUser
) -> Assignment | None:
    assignments = incomplete_loadable_assignments(session, current_user)
    return assignments[0] if assignments else None
