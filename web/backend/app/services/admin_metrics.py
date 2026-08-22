"""Bounded database projections for the synchronous admin metrics page."""

from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import Session, col, func, select

from app.models import (
    EvalItem,
    FactDecompReview,
    PooledCandidate,
    RelevanceJudgment,
    RetrievalQAReview,
    ReviewTask,
    User,
)
from app.schemas import AdminUserMetric


def user_activity_metrics(
    session: Session,
    users: Sequence[User],
    *,
    dataset_id: int | None,
) -> list[AdminUserMetric]:
    """Load activity counts for one bounded user page.

    Every query is a grouped projection restricted to the supplied user IDs.
    Agreement is intentionally a separate, explicitly bounded operation.
    """

    user_ids = [user.id for user in users if user.id is not None]
    if not user_ids:
        return []

    authored = select(col(EvalItem.author_user_id), func.count(col(EvalItem.id))).where(
        col(EvalItem.author_user_id).in_(user_ids)
    )
    fact_reviews = select(
        col(FactDecompReview.user_id), func.count(col(FactDecompReview.id))
    ).where(col(FactDecompReview.user_id).in_(user_ids))
    retrieval_reviews = select(
        col(RetrievalQAReview.user_id), func.count(col(RetrievalQAReview.id))
    ).where(col(RetrievalQAReview.user_id).in_(user_ids))
    relevance = select(
        col(RelevanceJudgment.user_id), func.count(col(RelevanceJudgment.id))
    ).where(col(RelevanceJudgment.user_id).in_(user_ids))
    if dataset_id is not None:
        authored = authored.where(col(EvalItem.dataset_id) == dataset_id)
        fact_reviews = fact_reviews.join(
            ReviewTask, col(FactDecompReview.task_id) == col(ReviewTask.id)
        ).where(col(ReviewTask.dataset_id) == dataset_id)
        retrieval_reviews = retrieval_reviews.join(
            EvalItem, col(RetrievalQAReview.item_id) == col(EvalItem.id)
        ).where(col(EvalItem.dataset_id) == dataset_id)
        relevance = relevance.join(
            PooledCandidate,
            col(RelevanceJudgment.candidate_id) == col(PooledCandidate.id),
        ).where(col(PooledCandidate.dataset_id) == dataset_id)

    authored_counts = dict(
        session.exec(authored.group_by(col(EvalItem.author_user_id))).all()
    )
    fact_review_counts = dict(
        session.exec(fact_reviews.group_by(col(FactDecompReview.user_id))).all()
    )
    retrieval_review_counts = dict(
        session.exec(retrieval_reviews.group_by(col(RetrievalQAReview.user_id))).all()
    )
    relevance_counts = dict(
        session.exec(relevance.group_by(col(RelevanceJudgment.user_id))).all()
    )

    rows = []
    for user in users:
        assert user.id is not None
        rows.append(
            AdminUserMetric(
                user_id=str(user.id),
                email=user.email,
                role=user.role.value,
                reviewer_kind=user.reviewer_kind.value,
                authored_items=authored_counts.get(user.id, 0),
                fact_decomp_reviews=fact_review_counts.get(user.id, 0),
                retrieval_qa_reviews=retrieval_review_counts.get(user.id, 0),
                relevance_judgments=relevance_counts.get(user.id, 0),
            )
        )
    return rows
