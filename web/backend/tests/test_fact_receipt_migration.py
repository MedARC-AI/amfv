"""Migration coverage for fact-save receipts and assignment terminal integrity."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.config import settings
from app.models import (
    Assignment,
    AssignmentMode,
    Dataset,
    EvalItem,
    EvalType,
    FactDecompSaveReceipt,
    ItemSource,
    ItemStatus,
    User,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "0005_drop_nice_scraper_tables"


def test_fact_receipt_migration_round_trip_preserves_assignment_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, config = _database(tmp_path, monkeypatch, "fact-receipt-roundtrip")
    command.upgrade(config, PREVIOUS_REVISION)
    user_id, item_id, assignment_id = _insert_state(database_url, released=False)

    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("assignment")
    } >= {"ck_assignment_single_terminal_state"}
    assert {
        column["name"] for column in inspector.get_columns("fact_decomp_save_receipt")
    } == {
        "id",
        "author_user_id",
        "request_id",
        "request_hash",
        "command",
        "item_id",
        "request_payload",
        "response_payload",
        "created_at",
        "updated_at",
    }

    with Session(engine) as session:
        receipt = FactDecompSaveReceipt(
            author_user_id=user_id,
            request_id="migration-request",
            request_hash="a" * 64,
            command="draft",
            item_id=item_id,
            request_payload={"source_text": "saved"},
            response_payload={"id": item_id},
        )
        session.add(receipt)
        session.commit()
        duplicate = FactDecompSaveReceipt(
            author_user_id=user_id,
            request_id="migration-request",
            request_hash="a" * 64,
            command="draft",
            item_id=item_id,
            request_payload={},
            response_payload={},
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text("UPDATE assignment SET released_at = :now WHERE id = :id"),
                    {"now": datetime.now(timezone.utc), "id": assignment_id},
                )
    engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)
    assert "fact_decomp_save_receipt" not in inspector.get_table_names()
    assert "ck_assignment_single_terminal_state" not in {
        constraint["name"]
        for constraint in inspector.get_check_constraints("assignment")
    }
    with engine.connect() as connection:
        completed_at, released_at = connection.execute(
            sa.text("SELECT completed_at, released_at FROM assignment WHERE id = :id"),
            {"id": assignment_id},
        ).one()
    assert completed_at is not None
    assert released_at is None
    engine.dispose()

    command.upgrade(config, "head")


def test_fact_receipt_migration_refuses_invalid_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, config = _database(tmp_path, monkeypatch, "invalid-terminal")
    command.upgrade(config, PREVIOUS_REVISION)
    _insert_state(database_url, released=True)

    with pytest.raises(RuntimeError, match="both completed and released"):
        command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    assert "fact_decomp_save_receipt" not in sa.inspect(engine).get_table_names()
    engine.dispose()


def _database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> tuple[str, Config]:
    database_url = f"sqlite:///{tmp_path / f'{name}.db'}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    return database_url, Config(str(BACKEND_ROOT / "alembic.ini"))


def _insert_state(database_url: str, *, released: bool) -> tuple[UUID, int, int]:
    engine = sa.create_engine(database_url)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        dataset = Dataset(
            name=f"migration-dataset-{uuid4()}",
            display_name="Migration dataset",
            eval_type=EvalType.FACT_DECOMP,
        )
        user = User(
            email=f"migration-user-{uuid4()}@example.com",
            hashed_password="not-used",
        )
        session.add(dataset)
        session.add(user)
        session.flush()
        assert dataset.id is not None
        assert user.id is not None
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.HUMAN,
            author_user_id=user.id,
            prompt_text="Migration fact source",
            status=ItemStatus.DRAFT,
        )
        session.add(item)
        session.flush()
        assert item.id is not None
        assignment = Assignment(
            dataset_id=dataset.id,
            mode=AssignmentMode.ITEM_AUDIT,
            target_id=item.id,
            user_id=user.id,
            completed_at=now,
            released_at=now if released else None,
        )
        session.add(assignment)
        session.commit()
        assert assignment.id is not None
        result = (user.id, item.id, assignment.id)
    engine.dispose()
    return result
