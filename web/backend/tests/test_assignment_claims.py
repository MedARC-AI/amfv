from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    Dataset,
    EvalItem,
    EvalType,
    ItemSource,
    ItemStatus,
    User,
)
from app.services.assignment import claim_assignment


@pytest.mark.parametrize(("required_labels", "expected_slots"), [(1, [0]), (2, [0, 1])])
def test_twenty_concurrent_claims_never_exceed_live_slot_capacity(
    tmp_path,
    required_labels: int,
    expected_slots: list[int],
) -> None:
    engine = _sqlite_engine(tmp_path / f"claims-{required_labels}.db")
    _seed_claim_target(engine, required_labels=required_labels)
    user_ids = _create_claimants(engine, count=20)
    barrier = Barrier(len(user_ids))

    with ThreadPoolExecutor(max_workers=len(user_ids)) as executor:
        outcomes = list(
            executor.map(
                lambda user_id: _claim_once(engine, user_id, barrier), user_ids
            )
        )

    assert outcomes.count("claimed") == required_labels
    assert outcomes.count("no_work") == len(user_ids) - required_labels
    with Session(engine) as session:
        assignments = session.exec(
            select(Assignment)
            .where(
                col(Assignment.mode) == AssignmentMode.ITEM_AUDIT,
                col(Assignment.released_at).is_(None),
            )
            .order_by(col(Assignment.slot))
        ).all()
    assert len(assignments) == required_labels
    assert [assignment.slot for assignment in assignments] == expected_slots
    assert all(
        assignment.kind
        == (AssignmentKind.DOUBLE if required_labels == 2 else AssignmentKind.REGULAR)
        for assignment in assignments
    )
    engine.dispose()


def _sqlite_engine(path) -> object:
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


def _seed_claim_target(engine, *, required_labels: int) -> None:  # noqa: ANN001
    with Session(engine) as session:
        author = User(email="author@example.com", hashed_password="x")
        dataset = Dataset(
            name="claim-target",
            display_name="Claim target",
            eval_type=EvalType.RETRIEVAL,
            double_rate=1 if required_labels == 2 else 0,
        )
        session.add(author)
        session.add(dataset)
        session.flush()
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            source=ItemSource.HUMAN,
            author_user_id=author.id,
            prompt_text="Claim me",
            status=ItemStatus.ACTIVE,
        )
        session.add(item)
        session.commit()


def _create_claimants(engine, *, count: int) -> list[UUID]:  # noqa: ANN001
    with Session(engine) as session:
        users = [
            User(email=f"claimant-{index}@example.com", hashed_password="x")
            for index in range(count)
        ]
        session.add_all(users)
        session.commit()
        return [user.id for user in users]


def _claim_once(engine, user_id: UUID, barrier: Barrier) -> str:  # noqa: ANN001
    barrier.wait()
    for attempt in range(20):
        with Session(engine) as session:
            user = session.get(User, user_id)
            assert user is not None
            try:
                assignment, created = claim_assignment(
                    session, user, AssignmentMode.ITEM_AUDIT
                )
                session.commit()
            except OperationalError:
                session.rollback()
                if attempt == 19:
                    raise
                time.sleep(0.01)
                continue
            if assignment is None:
                return "no_work"
            assert created
            return "claimed"
    raise AssertionError("Claim retry loop did not return")
