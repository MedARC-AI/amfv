"""Migration coverage for recoverable authoring receipts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from app.core.config import settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STATE_REVISION = "0002_canonical_review_assignment_state"


def test_recoverable_authoring_upgrade_and_downgrade_preserve_populated_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "recoverable-authoring.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))

    command.upgrade(config, STATE_REVISION)
    user_id = _insert_populated_state(database_url)

    command.upgrade(config, "0003_recoverable_authoring")
    _assert_receipt_table_and_populate_it(database_url, user_id)

    command.downgrade(config, STATE_REVISION)
    _assert_downgrade_keeps_existing_state(database_url, user_id)


def _insert_populated_state(database_url: str) -> str:
    engine = sa.create_engine(database_url)
    now = datetime.now(timezone.utc)
    user_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO dataset (
                    id, name, display_name, eval_type, double_rate, trap_rate,
                    is_active, created_at, updated_at
                ) VALUES (1, 'authoring', 'Authoring', 'RETRIEVAL', 0, 0, 1, :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO "user" (
                    id, email, is_active, is_superuser, hashed_password,
                    retrieval_dataset_id, created_at, updated_at
                ) VALUES (:id, 'author@example.com', 1, 0, 'hash', 1, :now, :now)
                """
            ),
            {"id": user_id, "now": now},
        )
    engine.dispose()
    return user_id


def _assert_receipt_table_and_populate_it(database_url: str, user_id: str) -> None:
    engine = sa.create_engine(database_url)
    now = datetime.now(timezone.utc)
    inspector = sa.inspect(engine)
    columns = {
        column["name"] for column in inspector.get_columns("retrieval_submission_batch")
    }
    assert columns == {
        "id",
        "author_user_id",
        "request_id",
        "request_hash",
        "created_item_ids",
        "created_at",
        "updated_at",
    }
    unique_constraints = inspector.get_unique_constraints("retrieval_submission_batch")
    assert unique_constraints == [
        {
            "name": "uq_retrieval_submission_batch_author_request",
            "column_names": ["author_user_id", "request_id"],
        }
    ]
    with engine.begin() as connection:
        values = {
            "author_user_id": user_id,
            "request_id": "client-request-1",
            "request_hash": "a" * 64,
            "created_item_ids": json.dumps([10, 11]),
            "now": now,
        }
        connection.execute(
            sa.text(
                """
                INSERT INTO retrieval_submission_batch (
                    author_user_id, request_id, request_hash, created_item_ids,
                    created_at, updated_at
                ) VALUES (
                    :author_user_id, :request_id, :request_hash, :created_item_ids,
                    :now, :now
                )
                """
            ),
            values,
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO retrieval_submission_batch (
                            author_user_id, request_id, request_hash, created_item_ids,
                            created_at, updated_at
                        ) VALUES (
                            :author_user_id, :request_id, :request_hash, :created_item_ids,
                            :now, :now
                        )
                        """
                    ),
                    values,
                )
        receipt = connection.execute(
            sa.text(
                """
                SELECT request_hash, created_item_ids
                FROM retrieval_submission_batch
                WHERE author_user_id = :author_user_id AND request_id = :request_id
                """
            ),
            values,
        ).one()
        assert receipt.request_hash == "a" * 64
        assert json.loads(receipt.created_item_ids) == [10, 11]
    engine.dispose()


def _assert_downgrade_keeps_existing_state(database_url: str, user_id: str) -> None:
    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)
    assert "retrieval_submission_batch" not in inspector.get_table_names()
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                """
                SELECT retrieval_dataset_id, fact_decomp_dataset_id
                FROM "user" WHERE id = :id
                """
            ),
            {"id": user_id},
        ).one()
    assert row == (1, None)
    engine.dispose()
