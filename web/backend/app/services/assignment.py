from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    Dataset,
    EvalType,
    User,
)
from app.services.assignment_lifecycle import complete_assignment, release_assignment
from app.services.assignment_queue import (
    _assignment_kind,
    _required_labels,
    assignment_target_is_loadable,
    available_item_audit_targets,
    available_relevance_candidates,
    incomplete_loadable_assignments,
    item_accepted_for_relevance,
    pooled_candidate_is_loadable,
)

__all__ = [
    "assignment_target_is_loadable",
    "available_item_audit_targets",
    "available_relevance_candidates",
    "claim_assignment",
    "complete_assignment",
    "get_current_or_first_dataset_readonly",
    "get_or_create_current_dataset",
    "incomplete_loadable_assignments",
    "item_accepted_for_relevance",
    "pooled_candidate_is_loadable",
    "release_assignment",
    "select_assignment",
]


def get_or_create_current_dataset(
    session: Session,
    user: User,
    *,
    eval_type: EvalType = EvalType.RETRIEVAL,
    dataset_id: int | None = None,
) -> Dataset | None:
    """Return a writable per-evaluation dataset preference for a claim command.

    The fallback is used only when the caller has no usable preference for the
    requested evaluation type. Read paths must use
    :func:`get_current_or_first_dataset_readonly` instead.
    """

    if dataset_id is not None:
        dataset = _active_dataset(session, dataset_id, eval_type)
        if dataset is None:
            return None
        _set_dataset_preference(session, user, dataset, eval_type)
        return dataset

    preference_id = _dataset_preference_id(user, eval_type)
    if preference_id is not None:
        dataset = _active_dataset(session, preference_id, eval_type)
        if dataset is not None:
            return dataset

    dataset = _first_active_dataset(session, eval_type)
    if dataset is not None:
        _set_dataset_preference(session, user, dataset, eval_type)
    return dataset


def get_current_or_first_dataset_readonly(
    session: Session,
    user: User,
    *,
    eval_type: EvalType = EvalType.RETRIEVAL,
) -> Dataset | None:
    """Read a type-specific preference without mutating the user or queue."""

    preference_id = _dataset_preference_id(user, eval_type)
    if preference_id is not None:
        dataset = _active_dataset(session, preference_id, eval_type)
        if dataset is not None:
            return dataset
    return _first_active_dataset(session, eval_type)


def claim_assignment(
    session: Session,
    user: User,
    mode: AssignmentMode,
    *,
    eval_type: EvalType = EvalType.RETRIEVAL,
    dataset_id: int | None = None,
) -> tuple[Assignment | None, bool]:
    """Atomically reserve a single typed label slot, when one is available.

    The two live partial unique indexes are the quota authority. A failed
    insert means another claimant has won that slot, so this function continues
    through the remaining slots and targets instead of relying on a stale count.
    """

    if eval_type != EvalType.RETRIEVAL:
        return None, False
    dataset = get_or_create_current_dataset(
        session,
        user,
        eval_type=eval_type,
        dataset_id=dataset_id,
    )
    if dataset is None:
        return None, False

    _serialize_claims_for_user(session, user)
    existing = _existing_loadable_assignment(session, user, dataset, mode)
    if existing is not None:
        return existing, False

    if mode == AssignmentMode.ITEM_AUDIT:
        for item in available_item_audit_targets(session, user, dataset):
            assert item.id is not None
            kind = _assignment_kind(
                dataset,
                mode,
                item.id,
                is_calibration=item.is_calibration,
                is_trap=item.is_trap,
            )
            assignment = _claim_target(
                session,
                user,
                dataset,
                mode,
                item.id,
                kind,
                item.id,
            )
            if assignment is not None:
                return assignment, True
            existing = _existing_loadable_assignment(session, user, dataset, mode)
            if existing is not None:
                return existing, False
        return None, False

    if mode == AssignmentMode.RELEVANCE:
        for candidate in available_relevance_candidates(session, user, dataset):
            assert candidate.id is not None
            kind = _assignment_kind(
                dataset,
                mode,
                candidate.id,
                is_calibration=candidate.is_calibration,
                is_trap=candidate.is_trap,
            )
            assignment = _claim_target(
                session,
                user,
                dataset,
                mode,
                candidate.id,
                kind,
                candidate.item_id * 1_000_000 + candidate.id,
            )
            if assignment is not None:
                return assignment, True
            existing = _existing_loadable_assignment(session, user, dataset, mode)
            if existing is not None:
                return existing, False
    return None, False


def select_assignment(
    session: Session,
    user: User,
    mode: AssignmentMode,
    *,
    eval_type: EvalType = EvalType.RETRIEVAL,
) -> Assignment | None:
    """Backward-compatible service wrapper for callers that issue a claim."""

    assignment, _created = claim_assignment(session, user, mode, eval_type=eval_type)
    return assignment


def _dataset_preference_id(user: User, eval_type: EvalType) -> int | None:
    if eval_type == EvalType.RETRIEVAL:
        return user.retrieval_dataset_id
    return user.fact_decomp_dataset_id


def _serialize_claims_for_user(session: Session, user: User) -> None:
    """Take a write lock on the claimant before examining their live claims.

    The live target indexes protect capacity. Updating the claimant row also
    serializes concurrent claims from that same user, so two requests cannot
    independently reserve different targets before either can observe the
    other's live assignment.
    """

    user.updated_at = datetime.now(timezone.utc)
    session.add(user)
    session.flush()


def _set_dataset_preference(
    session: Session,
    user: User,
    dataset: Dataset,
    eval_type: EvalType,
) -> None:
    assert dataset.id is not None
    if eval_type == EvalType.RETRIEVAL:
        user.retrieval_dataset_id = dataset.id
    else:
        user.fact_decomp_dataset_id = dataset.id
    user.updated_at = datetime.now(timezone.utc)
    session.add(user)
    session.flush()


def _active_dataset(
    session: Session,
    dataset_id: int,
    eval_type: EvalType,
) -> Dataset | None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active or dataset.eval_type != eval_type:
        return None
    return dataset


def _first_active_dataset(session: Session, eval_type: EvalType) -> Dataset | None:
    return session.exec(
        select(Dataset)
        .where(
            col(Dataset.is_active) == True,  # noqa: E712
            col(Dataset.eval_type) == eval_type,
        )
        .order_by(col(Dataset.display_name), col(Dataset.id))
    ).first()


def _existing_loadable_assignment(
    session: Session,
    user: User,
    dataset: Dataset,
    mode: AssignmentMode,
) -> Assignment | None:
    assignments = session.exec(
        select(Assignment)
        .where(
            col(Assignment.dataset_id) == dataset.id,
            col(Assignment.user_id) == user.id,
            col(Assignment.mode) == mode,
            col(Assignment.completed_at).is_(None),
            col(Assignment.released_at).is_(None),
        )
        .order_by(col(Assignment.assigned_at), col(Assignment.id))
    ).all()
    return next(
        (
            assignment
            for assignment in assignments
            if assignment_target_is_loadable(session, assignment)
        ),
        None,
    )


def _claim_target(
    session: Session,
    user: User,
    dataset: Dataset,
    mode: AssignmentMode,
    target_id: int,
    kind: AssignmentKind,
    position: int,
) -> Assignment | None:
    assert dataset.id is not None
    assert user.id is not None
    for slot in range(_required_labels(kind)):
        assignment = Assignment(
            dataset_id=dataset.id,
            user_id=user.id,
            mode=mode,
            target_id=target_id,
            kind=kind,
            position=position,
            slot=slot,
        )
        try:
            with session.begin_nested():
                session.add(assignment)
                session.flush()
        except IntegrityError:
            continue
        assert assignment.id is not None
        return assignment
    return None
