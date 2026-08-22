from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    CalibrationStatus,
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

    calibration_pending = not _calibration_complete(
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

    calibration_pending = not _calibration_complete(
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


def complete_assignment(session: Session, assignment: Assignment) -> None:
    """Complete a live assignment without releasing its consumed label slot."""

    if assignment.released_at is not None:
        raise ValueError("Released assignments cannot be completed")
    if assignment.completed_at is not None:
        raise ValueError("Completed assignments cannot be completed again")
    assignment.completed_at = datetime.now(timezone.utc)
    session.add(assignment)
    _maybe_complete_calibration(session, assignment)


def release_assignment(
    session: Session, assignment: Assignment, *, reason: str
) -> None:
    """Release an incomplete assignment so its slot becomes claimable again."""

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("Release reason is required")
    if len(normalized_reason) > 500:
        raise ValueError("Release reason must be at most 500 characters")
    if assignment.completed_at is not None:
        raise ValueError("Completed assignments cannot be released")
    if assignment.released_at is not None:
        raise ValueError("Assignment has already been released")
    assignment.released_at = datetime.now(timezone.utc)
    assignment.release_reason = normalized_reason
    assignment.updated_at = datetime.now(timezone.utc)
    session.add(assignment)


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


def _calibration_complete(
    session: Session,
    user: User,
    dataset: Dataset,
    mode: AssignmentMode,
) -> bool:
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
