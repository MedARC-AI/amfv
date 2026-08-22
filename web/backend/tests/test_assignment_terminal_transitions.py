from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.routes import review as review_routes
from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    Dataset,
    EvalItem,
    EvalType,
    ItemSource,
    ItemStatus,
    RetrievalQAReview,
    User,
)
from app.schemas import AssignmentRelease, RetrievalReviewSubmit


@pytest.mark.parametrize("race_index", range(8))
def test_release_and_retrieval_submission_have_one_terminal_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race_index: int,
) -> None:
    engine = _sqlite_engine(tmp_path / f"assignment-terminal-{race_index}.db")
    assignment_id, reviewer_id = _seed_retrieval_assignment(engine)
    terminal_barrier = Barrier(2)
    original_release = review_routes.release_assignment
    original_complete = review_routes.complete_assignment

    def synchronized_release(
        session: Session, assignment: Assignment, *, reason: str
    ) -> bool:
        terminal_barrier.wait(timeout=5)
        return original_release(session, assignment, reason=reason)

    def synchronized_complete(session: Session, assignment: Assignment) -> bool:
        terminal_barrier.wait(timeout=5)
        return original_complete(session, assignment)

    monkeypatch.setattr(review_routes, "release_assignment", synchronized_release)
    monkeypatch.setattr(review_routes, "complete_assignment", synchronized_complete)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda action: action(),
                [
                    lambda: _release_once(engine, assignment_id, reviewer_id),
                    lambda: _submit_retrieval_once(engine, assignment_id, reviewer_id),
                ],
            )
        )

    assert sorted(outcomes) in (["conflict", "released"], ["conflict", "submitted"])
    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        assert assignment is not None
        assert (assignment.completed_at is None) != (assignment.released_at is None)
        judgments = session.exec(
            select(RetrievalQAReview).where(
                RetrievalQAReview.assignment_id == assignment_id
            )
        ).all()
        if assignment.released_at is not None:
            assert judgments == []
        else:
            assert len(judgments) == 1
    engine.dispose()


def _sqlite_engine(path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(connection, _record) -> None:  # noqa: ANN001
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    SQLModel.metadata.create_all(engine)
    return engine


def _seed_retrieval_assignment(engine: Engine) -> tuple[int, UUID]:
    with Session(engine) as session:
        author = User(email="race-author@example.com", hashed_password="x")
        reviewer = User(email="race-reviewer@example.com", hashed_password="x")
        dataset = Dataset(
            name="assignment-terminal-race",
            display_name="Assignment terminal race",
            eval_type=EvalType.RETRIEVAL,
        )
        session.add_all([author, reviewer, dataset])
        session.flush()
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            source=ItemSource.HUMAN,
            author_user_id=author.id,
            prompt_text="Can this retrieval judgment race a release?",
            status=ItemStatus.ACTIVE,
        )
        session.add(item)
        session.flush()
        assignment = Assignment(
            dataset_id=dataset.id,
            user_id=reviewer.id,
            mode=AssignmentMode.ITEM_AUDIT,
            target_id=item.id,
            kind=AssignmentKind.REGULAR,
        )
        session.add(assignment)
        session.commit()
        assert assignment.id is not None
        return assignment.id, reviewer.id


def _release_once(engine: Engine, assignment_id: int, reviewer_id: UUID) -> str:
    with Session(engine) as session:
        reviewer = session.get(User, reviewer_id)
        assert reviewer is not None
        try:
            review_routes.release_review_assignment(
                session,
                assignment_id,
                AssignmentRelease(reason="Return the assignment to the queue."),
                reviewer,
            )
        except HTTPException as exc:
            _assert_terminal_conflict(exc, assignment_id)
            session.rollback()
            return "conflict"
        return "released"


def _submit_retrieval_once(
    engine: Engine, assignment_id: int, reviewer_id: UUID
) -> str:
    with Session(engine) as session:
        reviewer = session.get(User, reviewer_id)
        assert reviewer is not None
        body = RetrievalReviewSubmit(
            question_validity=4,
            evidence_quality=4,
            answer_correctness=4,
            answer_faithfulness=4,
            accept_as_gold=True,
            notes="Race-safe retrieval review.",
        )
        try:
            review_routes.submit_retrieval_review(
                session, assignment_id, body, reviewer
            )
        except HTTPException as exc:
            _assert_terminal_conflict(exc, assignment_id)
            session.rollback()
            return "conflict"
        return "submitted"


def _assert_terminal_conflict(error: HTTPException, assignment_id: int) -> None:
    assert error.status_code == 409
    assert error.detail == {
        "code": "assignment_terminal_conflict",
        "message": "Review assignment has already been completed or released.",
        "assignment_id": assignment_id,
    }
