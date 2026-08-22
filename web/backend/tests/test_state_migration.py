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
REVISION = "0001_initial_sqlite_template"
PRE_CHECKS_CLEANUP_REVISION = "0006_fact_save_receipts"


def test_canonical_state_upgrade_and_downgrade_preserve_populated_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "state-migration.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))

    command.upgrade(config, REVISION)
    _insert_source_shaped_rows(database_url)

    command.upgrade(config, "head")
    _assert_canonical_upgrade(database_url)

    command.downgrade(config, REVISION)
    _assert_source_shaped_downgrade(database_url)


def test_retrieval_checks_cleanup_refuses_disagreement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'review-checks-disagreement.db'}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, REVISION)
    _insert_source_shaped_rows(database_url)
    command.upgrade(config, PRE_CHECKS_CLEANUP_REVISION)

    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE retrieval_qa_review SET checks = :checks WHERE id = 1"),
            {
                "checks": json.dumps(
                    {
                        "question_validity": 4,
                        "evidence_quality": 3,
                        "answer_correctness": 4,
                        "answer_faithfulness": 2,
                        "accept_as_gold": False,
                        "notes": "migrated note",
                    }
                )
            },
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="disagrees with typed columns"):
        command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    assert "checks" in {
        column["name"]
        for column in sa.inspect(engine).get_columns("retrieval_qa_review")
    }
    engine.dispose()


def _insert_source_shaped_rows(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    now = datetime.now(timezone.utc)
    retrieval_user_id = uuid4()
    fact_user_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO dataset (
                    id, name, display_name, eval_type, double_rate, trap_rate,
                    is_active, created_at, updated_at
                ) VALUES
                    (1, 'retrieval', 'Retrieval', 'RETRIEVAL', 0, 0, 1, :now, :now),
                    (2, 'facts', 'Facts', 'FACT_DECOMP', 0, 0, 1, :now, :now)
                """
            ),
            {"now": now},
        )
        for user_id, email, dataset_id in (
            (retrieval_user_id, "retrieval@example.com", 1),
            (fact_user_id, "facts@example.com", 2),
        ):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO "user" (
                        id, email, is_active, is_superuser, hashed_password,
                        current_dataset_id, dataset_streak_remaining,
                        label_count_session, label_count_total,
                        created_item_count, items_authored_total, profile_completed,
                        created_at, updated_at
                    ) VALUES (
                        :id, :email, 1, 0, 'hash', :dataset_id, 0, 0, 0, 0, 0, 0,
                        :now, :now
                    )
                    """
                ),
                {
                    "id": user_id.hex,
                    "email": email,
                    "dataset_id": dataset_id,
                    "now": now,
                },
            )
        connection.execute(
            sa.text(
                """
                INSERT INTO eval_item (
                    id, dataset_id, eval_type, source, prompt_text, status,
                    revision, is_active, is_calibration, is_trap,
                    flagged_ambiguous, created_at, updated_at
                ) VALUES (1, 1, 'RETRIEVAL', 'HUMAN', 'Question?', 'ACTIVE', 1, 1, 0, 0, 0, :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO assignment (
                    id, dataset_id, mode, target_id, user_id, kind, assigned_at,
                    completed_at, position, created_at, updated_at
                ) VALUES (1, 1, 'ITEM_AUDIT', 1, :user_id, 'REGULAR', :now, NULL, 0, :now, :now)
                """
            ),
            {"user_id": retrieval_user_id.hex, "now": now},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO retrieval_qa_review (
                    id, assignment_id, item_id, user_id, checks, skipped,
                    started_at, submitted_at, created_at, updated_at
                ) VALUES (1, 1, 1, :user_id, :checks, 0, :now, :now, :now, :now)
                """
            ),
            {
                "user_id": retrieval_user_id.hex,
                "checks": json.dumps(
                    {
                        "question_validity": 4,
                        "evidence_quality": 3,
                        "answer_correctness": 4,
                        "answer_faithfulness": 2,
                        "accept_as_gold": True,
                        "notes": "migrated note",
                    }
                ),
                "now": now,
            },
        )
    engine.dispose()


def _assert_canonical_upgrade(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    assert "checks" not in {
        column["name"]
        for column in sa.inspect(engine).get_columns("retrieval_qa_review")
    }
    with engine.begin() as connection:
        retrieval_preference = connection.execute(
            sa.text(
                'SELECT retrieval_dataset_id, fact_decomp_dataset_id FROM "user" WHERE email = :email'
            ),
            {"email": "retrieval@example.com"},
        ).one()
        fact_preference = connection.execute(
            sa.text(
                'SELECT retrieval_dataset_id, fact_decomp_dataset_id FROM "user" WHERE email = :email'
            ),
            {"email": "facts@example.com"},
        ).one()
        assert retrieval_preference == (1, None)
        assert fact_preference == (None, 2)

        review = connection.execute(
            sa.text(
                """
                SELECT question_validity, evidence_quality, answer_correctness,
                       answer_faithfulness, notes, verdict, skipped, skip_reason
                FROM retrieval_qa_review WHERE id = 1
                """
            )
        ).one()
        assert review == (4, 3, 4, 2, "migrated note", "ACCEPT", 0, None)

        indexes = {
            row[0]: row[1]
            for row in connection.execute(
                sa.text(
                    """
                    SELECT name, sql FROM sqlite_master
                    WHERE type = 'index' AND name LIKE 'uq_assignment_live_%'
                    """
                )
            )
        }
        assert set(indexes) == {
            "uq_assignment_live_mode_target_user",
            "uq_assignment_live_mode_target_slot",
        }
        assert all("WHERE released_at IS NULL" in sql for sql in indexes.values())

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "UPDATE retrieval_qa_review SET question_validity = 5 WHERE id = 1"
                )
            )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "UPDATE retrieval_qa_review SET question_validity = NULL WHERE id = 1"
                )
            )
    engine.dispose()


def _assert_source_shaped_downgrade(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)
    user_columns = {column["name"] for column in inspector.get_columns("user")}
    review_columns = {
        column["name"] for column in inspector.get_columns("retrieval_qa_review")
    }
    assert "current_dataset_id" in user_columns
    assert "retrieval_dataset_id" not in user_columns
    assert "fact_decomp_dataset_id" not in user_columns
    assert "question_validity" not in review_columns
    assert "notes" not in review_columns
    with engine.connect() as connection:
        checks = connection.execute(
            sa.text("SELECT checks FROM retrieval_qa_review WHERE id = 1")
        ).scalar_one()
    assert json.loads(checks)["accept_as_gold"] is True
    engine.dispose()
