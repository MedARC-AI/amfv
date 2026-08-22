from __future__ import annotations

import hashlib

from sqlmodel import Session, col, func, select

from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    Chunk,
    Dataset,
    Document,
    EvalItem,
    EvalType,
    ItemStatus,
    ItemVerdict,
    PooledCandidate,
    RetrievalQAReview,
    User,
)
from app.services.assignment_lifecycle import calibration_complete

__all__ = [
    "assignment_target_is_loadable",
    "available_item_audit_targets",
    "available_relevance_candidates",
    "incomplete_loadable_assignments",
    "item_accepted_for_relevance",
    "pooled_candidate_is_loadable",
]


def incomplete_loadable_assignments(
    session: Session,
    user: User,
    *,
    mode: AssignmentMode | None = None,
    eval_type: EvalType = EvalType.RETRIEVAL,
) -> list[Assignment]:
    """Return only live, incomplete assignments that can still be rendered."""

    where = [
        col(Assignment.user_id) == user.id,
        col(Assignment.completed_at).is_(None),
        col(Assignment.released_at).is_(None),
        col(Dataset.is_active) == True,  # noqa: E712
        col(Dataset.eval_type) == eval_type,
    ]
    if mode is not None:
        where.append(col(Assignment.mode) == mode)
    assignments = session.exec(
        select(Assignment)
        .join(Dataset, col(Assignment.dataset_id) == col(Dataset.id))
        .where(*where)
        .order_by(col(Assignment.assigned_at), col(Assignment.id))
    ).all()
    return [
        assignment
        for assignment in assignments
        if assignment_target_is_loadable(session, assignment)
    ]


def available_item_audit_targets(
    session: Session,
    user: User,
    dataset: Dataset,
) -> list[EvalItem]:
    """Enumerate eligible retrieval items in the canonical claim order."""

    calibration_pending = not calibration_complete(
        session, user, dataset, AssignmentMode.ITEM_AUDIT
    )
    statement = (
        select(EvalItem)
        .where(
            col(EvalItem.dataset_id) == dataset.id,
            col(EvalItem.eval_type) == EvalType.RETRIEVAL,
            col(EvalItem.status) == ItemStatus.ACTIVE,
            col(EvalItem.is_active) == True,  # noqa: E712
        )
        .order_by(col(EvalItem.priority_tag).desc(), col(EvalItem.id))
    )
    if calibration_pending:
        statement = statement.where(col(EvalItem.is_calibration) == True)  # noqa: E712
    else:
        statement = statement.where(col(EvalItem.is_calibration) == False)  # noqa: E712

    available: list[EvalItem] = []
    for item in session.exec(statement).all():
        assert item.id is not None
        if item.author_user_id == user.id:
            continue
        if _already_assigned_to_user(session, user, AssignmentMode.ITEM_AUDIT, item.id):
            continue
        kind = _assignment_kind(
            dataset,
            AssignmentMode.ITEM_AUDIT,
            item.id,
            is_calibration=item.is_calibration,
            is_trap=item.is_trap,
        )
        if not _target_has_live_slot_capacity(
            session,
            AssignmentMode.ITEM_AUDIT,
            item.id,
            kind,
        ):
            continue
        available.append(item)
    return available


def available_relevance_candidates(
    session: Session,
    user: User,
    dataset: Dataset,
) -> list[PooledCandidate]:
    """Enumerate eligible relevance candidates in the canonical claim order."""

    calibration_pending = not calibration_complete(
        session, user, dataset, AssignmentMode.RELEVANCE
    )
    statement = (
        select(PooledCandidate)
        .where(col(PooledCandidate.dataset_id) == dataset.id)
        .order_by(col(PooledCandidate.item_id), col(PooledCandidate.id))
    )
    if calibration_pending:
        statement = statement.where(col(PooledCandidate.is_calibration) == True)  # noqa: E712
    else:
        statement = statement.where(col(PooledCandidate.is_calibration) == False)  # noqa: E712

    available: list[PooledCandidate] = []
    for candidate in session.exec(statement).all():
        assert candidate.id is not None
        item = session.get(EvalItem, candidate.item_id)
        if (
            item is None
            or item.dataset_id != candidate.dataset_id
            or item.eval_type != EvalType.RETRIEVAL
            or item.status != ItemStatus.ACTIVE
            or not item.is_active
            or item.author_user_id == user.id
        ):
            continue
        if (
            not candidate.is_calibration
            and not candidate.is_trap
            and not item_accepted_for_relevance(session, item)
        ):
            continue
        if _already_assigned_to_user(
            session, user, AssignmentMode.RELEVANCE, candidate.id
        ):
            continue
        if not pooled_candidate_is_loadable(session, candidate):
            continue
        kind = _assignment_kind(
            dataset,
            AssignmentMode.RELEVANCE,
            candidate.id,
            is_calibration=candidate.is_calibration,
            is_trap=candidate.is_trap,
        )
        if not _target_has_live_slot_capacity(
            session,
            AssignmentMode.RELEVANCE,
            candidate.id,
            kind,
        ):
            continue
        available.append(candidate)
    return available


def assignment_target_is_loadable(session: Session, assignment: Assignment) -> bool:
    """Check that a live claim still points at active retrieval work."""

    if assignment.released_at is not None:
        return False
    dataset = session.get(Dataset, assignment.dataset_id)
    if (
        dataset is None
        or not dataset.is_active
        or dataset.eval_type != EvalType.RETRIEVAL
    ):
        return False
    if assignment.mode == AssignmentMode.ITEM_AUDIT:
        item = session.get(EvalItem, assignment.target_id)
        return bool(
            item
            and item.dataset_id == assignment.dataset_id
            and item.eval_type == EvalType.RETRIEVAL
            and item.status == ItemStatus.ACTIVE
            and item.is_active
        )
    if assignment.mode == AssignmentMode.RELEVANCE:
        candidate = session.get(PooledCandidate, assignment.target_id)
        if candidate is None or candidate.dataset_id != assignment.dataset_id:
            return False
        item = session.get(EvalItem, candidate.item_id)
        if (
            item is None
            or item.dataset_id != assignment.dataset_id
            or item.eval_type != EvalType.RETRIEVAL
            or item.status != ItemStatus.ACTIVE
            or not item.is_active
        ):
            return False
        return pooled_candidate_is_loadable(session, candidate)
    return False


def pooled_candidate_is_loadable(session: Session, candidate: PooledCandidate) -> bool:
    """Check the candidate's evidence source without changing queue state."""

    chunk = session.get(Chunk, candidate.chunk_id)
    if chunk is None or chunk.dataset_id != candidate.dataset_id:
        return False
    document = session.get(Document, chunk.document_id)
    return bool(
        document
        and document.id == chunk.document_id
        and document.dataset_id == candidate.dataset_id
        and document.is_active
    )


def item_accepted_for_relevance(session: Session, item: EvalItem) -> bool:
    """Apply relevance eligibility to canonical typed retrieval-review state."""

    if item.flagged_ambiguous:
        return False
    judgments = list(
        session.exec(
            select(RetrievalQAReview).where(
                col(RetrievalQAReview.item_id) == item.id,
                col(RetrievalQAReview.skipped) == False,  # noqa: E712
            )
        ).all()
    )
    if not judgments:
        return False
    return all(
        judgment.verdict == ItemVerdict.ACCEPT
        and _has_complete_retrieval_rubric(judgment)
        for judgment in judgments
    )


def _assignment_kind(
    dataset: Dataset,
    mode: AssignmentMode,
    target_id: int,
    *,
    is_calibration: bool,
    is_trap: bool,
) -> AssignmentKind:
    if is_calibration:
        return AssignmentKind.CALIBRATION
    if is_trap:
        return AssignmentKind.TRAP
    if _rate_hit(dataset.double_rate, mode, target_id, "double"):
        return AssignmentKind.DOUBLE
    return AssignmentKind.REGULAR


def _required_labels(kind: AssignmentKind) -> int:
    return 2 if kind == AssignmentKind.DOUBLE else 1


def _target_has_live_slot_capacity(
    session: Session,
    mode: AssignmentMode,
    target_id: int,
    kind: AssignmentKind,
) -> bool:
    """Read live-slot availability without treating a count as race authority."""

    live_assignments = session.exec(
        select(func.count(col(Assignment.id))).where(
            col(Assignment.mode) == mode,
            col(Assignment.target_id) == target_id,
            col(Assignment.released_at).is_(None),
        )
    ).one()
    return live_assignments < _required_labels(kind)


def _already_assigned_to_user(
    session: Session,
    user: User,
    mode: AssignmentMode,
    target_id: int,
) -> bool:
    return bool(
        session.exec(
            select(Assignment).where(
                col(Assignment.user_id) == user.id,
                col(Assignment.mode) == mode,
                col(Assignment.target_id) == target_id,
                col(Assignment.released_at).is_(None),
            )
        ).first()
    )


def _has_complete_retrieval_rubric(judgment: RetrievalQAReview) -> bool:
    return all(
        score is not None and 1 <= score <= 4
        for score in (
            judgment.question_validity,
            judgment.evidence_quality,
            judgment.answer_correctness,
            judgment.answer_faithfulness,
        )
    )


def _rate_hit(rate: float, mode: AssignmentMode, target_id: int, salt: str) -> bool:
    if rate <= 0:
        return False
    bucket = (
        int(
            hashlib.sha256(f"{mode.value}:{target_id}:{salt}".encode()).hexdigest()[:8],
            16,
        )
        % 10_000
    )
    return bucket < int(rate * 10_000)
