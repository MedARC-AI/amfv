from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlmodel import Session, func, select

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


def get_or_create_current_dataset(session: Session, user: User, *, eval_type: EvalType = EvalType.RETRIEVAL) -> Dataset | None:
    if user.current_dataset_id:
        dataset = session.get(Dataset, user.current_dataset_id)
        if dataset and dataset.is_active and dataset.eval_type == eval_type:
            return dataset
    dataset = session.exec(
        select(Dataset).where(Dataset.is_active == True, Dataset.eval_type == eval_type).order_by(Dataset.display_name)  # noqa: E712
    ).first()
    if dataset:
        user.current_dataset_id = dataset.id
        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        session.flush()
    return dataset


def get_current_or_first_dataset_readonly(
    session: Session,
    user: User,
    *,
    eval_type: EvalType = EvalType.RETRIEVAL,
) -> Dataset | None:
    if user.current_dataset_id:
        dataset = session.get(Dataset, user.current_dataset_id)
        if dataset and dataset.is_active and dataset.eval_type == eval_type:
            return dataset
    return session.exec(
        select(Dataset)
        .where(
            Dataset.is_active == True,  # noqa: E712
            Dataset.eval_type == eval_type,
        )
        .order_by(Dataset.display_name)
    ).first()


def incomplete_loadable_assignments(
    session: Session,
    user: User,
    *,
    mode: AssignmentMode | None = None,
    eval_type: EvalType = EvalType.RETRIEVAL,
) -> list[Assignment]:
    where = [
        Assignment.user_id == user.id,
        Assignment.completed_at.is_(None),
        Dataset.is_active == True,  # noqa: E712
        Dataset.eval_type == eval_type,
    ]
    if mode is not None:
        where.append(Assignment.mode == mode)
    assignments = session.exec(
        select(Assignment)
        .join(Dataset, Assignment.dataset_id == Dataset.id)
        .where(*where)
        .order_by(Assignment.assigned_at)
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
    calibration_pending = not _calibration_complete(
        session, user, dataset, AssignmentMode.ITEM_AUDIT
    )
    statement = (
        select(EvalItem)
        .where(
            EvalItem.dataset_id == dataset.id,
            EvalItem.eval_type == EvalType.RETRIEVAL,
            EvalItem.status == ItemStatus.ACTIVE,
            EvalItem.is_active == True,  # noqa: E712
        )
        .order_by(EvalItem.priority_tag.desc(), EvalItem.id)
    )
    if calibration_pending:
        statement = statement.where(EvalItem.is_calibration == True)  # noqa: E712
    else:
        statement = statement.where(EvalItem.is_calibration == False)  # noqa: E712
    available: list[EvalItem] = []
    for item in session.exec(statement).all():
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
        required_labels = 2 if kind == AssignmentKind.DOUBLE else 1
        if _completed_count(session, AssignmentMode.ITEM_AUDIT, item.id) >= required_labels:
            continue
        available.append(item)
    return available


def available_relevance_candidates(
    session: Session,
    user: User,
    dataset: Dataset,
) -> list[PooledCandidate]:
    calibration_pending = not _calibration_complete(
        session, user, dataset, AssignmentMode.RELEVANCE
    )
    statement = (
        select(PooledCandidate)
        .where(PooledCandidate.dataset_id == dataset.id)
        .order_by(PooledCandidate.item_id, PooledCandidate.id)
    )
    if calibration_pending:
        statement = statement.where(PooledCandidate.is_calibration == True)  # noqa: E712
    else:
        statement = statement.where(PooledCandidate.is_calibration == False)  # noqa: E712
    available: list[PooledCandidate] = []
    for candidate in session.exec(statement).all():
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
        if _already_assigned_to_user(session, user, AssignmentMode.RELEVANCE, candidate.id):
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
        required_labels = 2 if kind == AssignmentKind.DOUBLE else 1
        if _completed_count(session, AssignmentMode.RELEVANCE, candidate.id) >= required_labels:
            continue
        available.append(candidate)
    return available


def assignment_target_is_loadable(session: Session, assignment: Assignment) -> bool:
    dataset = session.get(Dataset, assignment.dataset_id)
    if dataset is None or not dataset.is_active or dataset.eval_type != EvalType.RETRIEVAL:
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
    if item.flagged_ambiguous:
        return False
    judgments = list(
        session.exec(
            select(RetrievalQAReview).where(
                RetrievalQAReview.item_id == item.id,
                RetrievalQAReview.skipped == False,  # noqa: E712
            )
        ).all()
    )
    if not judgments:
        return False
    return all(judgment.verdict == ItemVerdict.ACCEPT for judgment in judgments)


def select_assignment(
    session: Session,
    user: User,
    mode: AssignmentMode,
    *,
    eval_type: EvalType = EvalType.RETRIEVAL,
) -> Assignment | None:
    dataset = get_or_create_current_dataset(session, user, eval_type=eval_type)
    if dataset is None:
        return None
    while existing := _existing_incomplete_assignment(session, user, dataset, mode):
        if assignment_target_is_loadable(session, existing):
            return existing
        existing.completed_at = datetime.now(timezone.utc)
        session.add(existing)
        session.flush()
    if mode == AssignmentMode.ITEM_AUDIT:
        return _select_item_audit_assignment(session, user, dataset)
    if mode == AssignmentMode.RELEVANCE:
        return _select_relevance_assignment(session, user, dataset)
    return None


def _existing_incomplete_assignment(
    session: Session,
    user: User,
    dataset: Dataset,
    mode: AssignmentMode,
) -> Assignment | None:
    return session.exec(
        select(Assignment)
        .where(
            Assignment.dataset_id == dataset.id,
            Assignment.user_id == user.id,
            Assignment.mode == mode,
            Assignment.completed_at.is_(None),
        )
        .order_by(Assignment.assigned_at)
    ).first()


def complete_assignment(session: Session, assignment: Assignment) -> None:
    assignment.completed_at = datetime.now(timezone.utc)
    session.add(assignment)
    _maybe_complete_calibration(session, assignment)


def _select_item_audit_assignment(session: Session, user: User, dataset: Dataset) -> Assignment | None:
    for item in available_item_audit_targets(session, user, dataset):
        kind = _assignment_kind(dataset, AssignmentMode.ITEM_AUDIT, item.id, is_calibration=item.is_calibration, is_trap=item.is_trap)
        return _create_assignment(session, user, dataset, AssignmentMode.ITEM_AUDIT, item.id, kind, item.id)
    return None


def _select_relevance_assignment(session: Session, user: User, dataset: Dataset) -> Assignment | None:
    for candidate in available_relevance_candidates(session, user, dataset):
        kind = _assignment_kind(
            dataset,
            AssignmentMode.RELEVANCE,
            candidate.id,
            is_calibration=candidate.is_calibration,
            is_trap=candidate.is_trap,
        )
        return _create_assignment(session, user, dataset, AssignmentMode.RELEVANCE, candidate.id, kind, candidate.item_id * 1_000_000 + candidate.id)
    return None


def _item_accepted_for_relevance(session: Session, item: EvalItem) -> bool:
    return item_accepted_for_relevance(session, item)


def _create_assignment(
    session: Session,
    user: User,
    dataset: Dataset,
    mode: AssignmentMode,
    target_id: int,
    kind: AssignmentKind,
    position: int,
) -> Assignment:
    assignment = Assignment(dataset_id=dataset.id, user_id=user.id, mode=mode, target_id=target_id, kind=kind, position=position)
    session.add(assignment)
    session.flush()
    return assignment


def _already_assigned_to_user(session: Session, user: User, mode: AssignmentMode, target_id: int) -> bool:
    return bool(
        session.exec(
            select(Assignment).where(Assignment.user_id == user.id, Assignment.mode == mode, Assignment.target_id == target_id)
        ).first()
    )


def _completed_count(session: Session, mode: AssignmentMode, target_id: int) -> int:
    return session.exec(
        select(func.count(Assignment.id)).where(
            Assignment.mode == mode,
            Assignment.target_id == target_id,
            Assignment.completed_at.is_not(None),
        )
    ).one()


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
    bucket = int(hashlib.sha256(f"{mode.value}:{target_id}:{salt}".encode()).hexdigest()[:8], 16) % 10_000
    return bucket < int(rate * 10_000)


def _calibration_complete(session: Session, user: User, dataset: Dataset, mode: AssignmentMode) -> bool:
    if session.exec(
        select(CalibrationStatus).where(CalibrationStatus.user_id == user.id, CalibrationStatus.dataset_id == dataset.id)
    ).first():
        return True
    if mode == AssignmentMode.ITEM_AUDIT:
        calibration_items = session.exec(
            select(EvalItem.id).where(
                EvalItem.dataset_id == dataset.id,
                EvalItem.eval_type == EvalType.RETRIEVAL,
                EvalItem.is_calibration == True,  # noqa: E712
            )
        ).all()
        return all(_completed_by_user(session, user.id, AssignmentMode.ITEM_AUDIT, item_id) for item_id in calibration_items)
    calibration_candidates = session.exec(
        select(PooledCandidate.id).where(
            PooledCandidate.dataset_id == dataset.id,
            PooledCandidate.is_calibration == True,  # noqa: E712
        )
    ).all()
    return all(
        _completed_by_user(session, user.id, AssignmentMode.RELEVANCE, candidate_id)
        for candidate_id in calibration_candidates
    )


def _dataset_has_calibration_targets(session: Session, dataset: Dataset) -> bool:
    has_items = session.exec(
        select(EvalItem.id).where(
            EvalItem.dataset_id == dataset.id,
            EvalItem.eval_type == EvalType.RETRIEVAL,
            EvalItem.is_calibration == True,  # noqa: E712
        )
    ).first()
    if has_items is not None:
        return True
    has_candidates = session.exec(
        select(PooledCandidate.id).where(
            PooledCandidate.dataset_id == dataset.id,
            PooledCandidate.is_calibration == True,  # noqa: E712
        )
    ).first()
    return has_candidates is not None


def _maybe_complete_calibration(session: Session, assignment: Assignment) -> None:
    if assignment.kind != AssignmentKind.CALIBRATION:
        return
    if _has_incomplete_calibration_targets(session, assignment):
        return
    existing = session.exec(
        select(CalibrationStatus).where(
            CalibrationStatus.user_id == assignment.user_id,
            CalibrationStatus.dataset_id == assignment.dataset_id,
        )
    ).first()
    if existing is None:
        session.add(CalibrationStatus(user_id=assignment.user_id, dataset_id=assignment.dataset_id))


def _has_incomplete_calibration_targets(session: Session, assignment: Assignment) -> bool:
    calibration_items = session.exec(
        select(EvalItem.id).where(
            EvalItem.dataset_id == assignment.dataset_id,
            EvalItem.eval_type == EvalType.RETRIEVAL,
            EvalItem.is_calibration == True,  # noqa: E712
        )
    ).all()
    for item_id in calibration_items:
        if not _completed_by_user(session, assignment.user_id, AssignmentMode.ITEM_AUDIT, item_id):
            return True
    calibration_candidates = session.exec(
        select(PooledCandidate.id).where(
            PooledCandidate.dataset_id == assignment.dataset_id,
            PooledCandidate.is_calibration == True,  # noqa: E712
        )
    ).all()
    for candidate_id in calibration_candidates:
        if not _completed_by_user(session, assignment.user_id, AssignmentMode.RELEVANCE, candidate_id):
            return True
    return False


def _completed_by_user(session: Session, user_id: UUID, mode: AssignmentMode, target_id: int) -> bool:
    return bool(
        session.exec(
            select(Assignment).where(
                Assignment.user_id == user_id,
                Assignment.mode == mode,
                Assignment.target_id == target_id,
                Assignment.completed_at.is_not(None),
            )
        ).first()
    )
