from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    CalibrationStatus,
    Dataset,
    EvalItem,
    EvalType,
    PooledCandidate,
    User,
)

__all__ = ["calibration_complete", "complete_assignment", "release_assignment"]


def complete_assignment(session: Session, assignment: Assignment) -> bool:
    """Atomically complete an assignment and report whether this call won."""

    assert assignment.id is not None
    completed_at = datetime.now(timezone.utc)
    completed_assignment_id = session.exec(
        update(Assignment)
        .where(
            col(Assignment.id) == assignment.id,
            col(Assignment.completed_at).is_(None),
            col(Assignment.released_at).is_(None),
        )
        .values(completed_at=completed_at, updated_at=completed_at)
        .returning(col(Assignment.id))
    ).first()
    if completed_assignment_id is None:
        return False
    session.expire(assignment)
    _maybe_complete_calibration(session, assignment)
    return True


def release_assignment(
    session: Session, assignment: Assignment, *, reason: str
) -> bool:
    """Atomically release an assignment and report whether this call won."""

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("Release reason is required")
    if len(normalized_reason) > 500:
        raise ValueError("Release reason must be at most 500 characters")
    assert assignment.id is not None
    released_at = datetime.now(timezone.utc)
    released_assignment_id = session.exec(
        update(Assignment)
        .where(
            col(Assignment.id) == assignment.id,
            col(Assignment.completed_at).is_(None),
            col(Assignment.released_at).is_(None),
        )
        .values(
            released_at=released_at,
            release_reason=normalized_reason,
            updated_at=released_at,
        )
        .returning(col(Assignment.id))
    ).first()
    if released_assignment_id is None:
        return False
    session.expire(assignment)
    return True


def calibration_complete(
    session: Session,
    user: User,
    dataset: Dataset,
    mode: AssignmentMode,
) -> bool:
    """Report whether a user completed the calibration targets for one mode."""

    if session.exec(
        select(CalibrationStatus).where(
            col(CalibrationStatus.user_id) == user.id,
            col(CalibrationStatus.dataset_id) == dataset.id,
        )
    ).first():
        return True
    if mode == AssignmentMode.ITEM_AUDIT:
        calibration_items = session.exec(
            select(EvalItem.id).where(
                col(EvalItem.dataset_id) == dataset.id,
                col(EvalItem.eval_type) == EvalType.RETRIEVAL,
                col(EvalItem.is_calibration) == True,  # noqa: E712
            )
        ).all()
        return all(
            _completed_by_user(session, user.id, AssignmentMode.ITEM_AUDIT, item_id)
            for item_id in calibration_items
        )
    calibration_candidates = session.exec(
        select(PooledCandidate.id).where(
            col(PooledCandidate.dataset_id) == dataset.id,
            col(PooledCandidate.is_calibration) == True,  # noqa: E712
        )
    ).all()
    return all(
        _completed_by_user(session, user.id, AssignmentMode.RELEVANCE, candidate_id)
        for candidate_id in calibration_candidates
    )


def _maybe_complete_calibration(session: Session, assignment: Assignment) -> None:
    if assignment.kind != AssignmentKind.CALIBRATION:
        return
    if _has_incomplete_calibration_targets(session, assignment):
        return
    existing = session.exec(
        select(CalibrationStatus).where(
            col(CalibrationStatus.user_id) == assignment.user_id,
            col(CalibrationStatus.dataset_id) == assignment.dataset_id,
        )
    ).first()
    if existing is None:
        session.add(
            CalibrationStatus(
                user_id=assignment.user_id,
                dataset_id=assignment.dataset_id,
            )
        )


def _has_incomplete_calibration_targets(
    session: Session, assignment: Assignment
) -> bool:
    calibration_items = session.exec(
        select(EvalItem.id).where(
            col(EvalItem.dataset_id) == assignment.dataset_id,
            col(EvalItem.eval_type) == EvalType.RETRIEVAL,
            col(EvalItem.is_calibration) == True,  # noqa: E712
        )
    ).all()
    for item_id in calibration_items:
        if not _completed_by_user(
            session,
            assignment.user_id,
            AssignmentMode.ITEM_AUDIT,
            item_id,
        ):
            return True
    calibration_candidates = session.exec(
        select(PooledCandidate.id).where(
            col(PooledCandidate.dataset_id) == assignment.dataset_id,
            col(PooledCandidate.is_calibration) == True,  # noqa: E712
        )
    ).all()
    for candidate_id in calibration_candidates:
        if not _completed_by_user(
            session,
            assignment.user_id,
            AssignmentMode.RELEVANCE,
            candidate_id,
        ):
            return True
    return False


def _completed_by_user(
    session: Session,
    user_id: UUID,
    mode: AssignmentMode,
    target_id: int,
) -> bool:
    return bool(
        session.exec(
            select(Assignment).where(
                col(Assignment.user_id) == user_id,
                col(Assignment.mode) == mode,
                col(Assignment.target_id) == target_id,
                col(Assignment.completed_at).is_not(None),
                col(Assignment.released_at).is_(None),
            )
        ).first()
    )
