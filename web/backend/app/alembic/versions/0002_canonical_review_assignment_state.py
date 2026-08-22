"""Establish canonical review, assignment, and dataset-preference state.

Revision ID: 0002_canonical_review_assignment_state
Revises: 0001_initial_sqlite_template
Create Date: 2026-08-22 00:00:00.000000

"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0002_canonical_review_assignment_state"
down_revision = "0001_initial_sqlite_template"
branch_labels = None
depends_on = None


RUBRIC_COLUMNS = (
    "question_validity",
    "evidence_quality",
    "answer_correctness",
    "answer_faithfulness",
)


def upgrade() -> None:
    _upgrade_user_dataset_preferences()
    _upgrade_assignment_slots()
    _upgrade_retrieval_reviews()


def downgrade() -> None:
    _downgrade_retrieval_reviews()
    _downgrade_assignment_slots()
    _downgrade_user_dataset_preferences()


def _upgrade_user_dataset_preferences() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("retrieval_dataset_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("fact_decomp_dataset_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_retrieval_dataset_id_dataset",
            "dataset",
            ["retrieval_dataset_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_user_fact_decomp_dataset_id_dataset",
            "dataset",
            ["fact_decomp_dataset_id"],
            ["id"],
        )

    bind.execute(
        sa.text(
            """
            UPDATE "user"
            SET retrieval_dataset_id = current_dataset_id
            WHERE current_dataset_id IN (
                SELECT id FROM dataset WHERE eval_type = 'RETRIEVAL'
            )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE "user"
            SET fact_decomp_dataset_id = current_dataset_id
            WHERE current_dataset_id IN (
                SELECT id FROM dataset WHERE eval_type = 'FACT_DECOMP'
            )
            """
        )
    )

    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("fk_user_current_dataset_id_dataset", type_="foreignkey")
        batch_op.drop_index("ix_user_current_dataset_id")
        batch_op.drop_column("current_dataset_id")

    op.create_index("ix_user_retrieval_dataset_id", "user", ["retrieval_dataset_id"])
    op.create_index("ix_user_fact_decomp_dataset_id", "user", ["fact_decomp_dataset_id"])


def _downgrade_user_dataset_preferences() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("current_dataset_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_current_dataset_id_dataset",
            "dataset",
            ["current_dataset_id"],
            ["id"],
        )

    bind.execute(
        sa.text(
            """
            UPDATE "user"
            SET current_dataset_id = COALESCE(retrieval_dataset_id, fact_decomp_dataset_id)
            """
        )
    )

    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("fk_user_retrieval_dataset_id_dataset", type_="foreignkey")
        batch_op.drop_constraint("fk_user_fact_decomp_dataset_id_dataset", type_="foreignkey")
        batch_op.drop_index("ix_user_retrieval_dataset_id")
        batch_op.drop_index("ix_user_fact_decomp_dataset_id")
        batch_op.drop_column("retrieval_dataset_id")
        batch_op.drop_column("fact_decomp_dataset_id")

    op.create_index("ix_user_current_dataset_id", "user", ["current_dataset_id"])


def _upgrade_assignment_slots() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("assignment") as batch_op:
        batch_op.add_column(sa.Column("slot", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("release_reason", sa.String(length=500), nullable=True))

    rows = bind.execute(
        sa.text(
            """
            SELECT id, mode, target_id, assigned_at
            FROM assignment
            ORDER BY mode, target_id, assigned_at, id
            """
        )
    ).mappings()
    slot_by_target: dict[tuple[str, int], int] = defaultdict(int)
    for row in rows:
        key = (str(row["mode"]), int(row["target_id"]))
        slot = slot_by_target[key]
        slot_by_target[key] += 1
        bind.execute(
            sa.text("UPDATE assignment SET slot = :slot WHERE id = :id"),
            {"slot": slot, "id": row["id"]},
        )

    with op.batch_alter_table("assignment") as batch_op:
        batch_op.alter_column("slot", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_constraint("uq_assignment_mode_target_user", type_="unique")
        batch_op.create_check_constraint("ck_assignment_slot_nonnegative", "slot >= 0")

    op.create_index("ix_assignment_released_at", "assignment", ["released_at"])
    op.create_index(
        "uq_assignment_live_mode_target_user",
        "assignment",
        ["mode", "target_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("released_at IS NULL"),
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "uq_assignment_live_mode_target_slot",
        "assignment",
        ["mode", "target_id", "slot"],
        unique=True,
        sqlite_where=sa.text("released_at IS NULL"),
        postgresql_where=sa.text("released_at IS NULL"),
    )


def _downgrade_assignment_slots() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT mode, target_id, user_id
            FROM assignment
            GROUP BY mode, target_id, user_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade assignment state while released/reclaimed assignments "
            "would violate the legacy per-user target uniqueness constraint."
        )

    op.drop_index("uq_assignment_live_mode_target_slot", table_name="assignment")
    op.drop_index("uq_assignment_live_mode_target_user", table_name="assignment")
    op.drop_index("ix_assignment_released_at", table_name="assignment")
    with op.batch_alter_table("assignment") as batch_op:
        batch_op.drop_constraint("ck_assignment_slot_nonnegative", type_="check")
        batch_op.create_unique_constraint(
            "uq_assignment_mode_target_user",
            ["mode", "target_id", "user_id"],
        )
        batch_op.drop_column("slot")
        batch_op.drop_column("released_at")
        batch_op.drop_column("release_reason")


def _upgrade_retrieval_reviews() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("retrieval_qa_review") as batch_op:
        for column in RUBRIC_COLUMNS:
            batch_op.add_column(sa.Column(column, sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))

    rows = bind.execute(
        sa.text(
            """
            SELECT id, checks, verdict, skipped, skip_reason
            FROM retrieval_qa_review
            ORDER BY id
            """
        )
    ).mappings()
    for row in rows:
        values = _canonical_review_values(row)
        bind.execute(
            sa.text(
                """
                UPDATE retrieval_qa_review
                SET question_validity = :question_validity,
                    evidence_quality = :evidence_quality,
                    answer_correctness = :answer_correctness,
                    answer_faithfulness = :answer_faithfulness,
                    notes = :notes,
                    verdict = :verdict,
                    skipped = :skipped,
                    skip_reason = :skip_reason
                WHERE id = :id
                """
            ),
            {"id": row["id"], **values},
        )

    with op.batch_alter_table("retrieval_qa_review") as batch_op:
        for column in RUBRIC_COLUMNS:
            batch_op.create_check_constraint(
                f"ck_retrieval_qa_review_{column}_range",
                f"{column} IS NULL OR {column} BETWEEN 1 AND 4",
            )
        batch_op.create_check_constraint(
            "ck_retrieval_qa_review_submission_shape",
            "("
            "skipped = TRUE "
            "AND skip_reason IS NOT NULL "
            "AND length(trim(skip_reason)) > 0 "
            "AND question_validity IS NULL "
            "AND evidence_quality IS NULL "
            "AND answer_correctness IS NULL "
            "AND answer_faithfulness IS NULL "
            "AND verdict IS NULL"
            ") OR ("
            "skipped = FALSE "
            "AND skip_reason IS NULL "
            "AND question_validity IS NOT NULL "
            "AND question_validity BETWEEN 1 AND 4 "
            "AND evidence_quality IS NOT NULL "
            "AND evidence_quality BETWEEN 1 AND 4 "
            "AND answer_correctness IS NOT NULL "
            "AND answer_correctness BETWEEN 1 AND 4 "
            "AND answer_faithfulness IS NOT NULL "
            "AND answer_faithfulness BETWEEN 1 AND 4 "
            "AND verdict IS NOT NULL "
            "AND verdict IN ('ACCEPT', 'REJECT')"
            ")",
        )


def _downgrade_retrieval_reviews() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, checks, question_validity, evidence_quality,
                   answer_correctness, answer_faithfulness, notes, verdict,
                   skipped, skip_reason
            FROM retrieval_qa_review
            ORDER BY id
            """
        )
    ).mappings()
    for row in rows:
        checks = _json_object(row["checks"])
        if not bool(row["skipped"]):
            checks.update(
                {
                    "question_validity": row["question_validity"],
                    "evidence_quality": row["evidence_quality"],
                    "answer_correctness": row["answer_correctness"],
                    "answer_faithfulness": row["answer_faithfulness"],
                    "accept_as_gold": row["verdict"] == "ACCEPT",
                    "notes": row["notes"],
                }
            )
        bind.execute(
            sa.text("UPDATE retrieval_qa_review SET checks = :checks WHERE id = :id"),
            {"id": row["id"], "checks": json.dumps(checks)},
        )

    with op.batch_alter_table("retrieval_qa_review") as batch_op:
        batch_op.drop_constraint("ck_retrieval_qa_review_submission_shape", type_="check")
        for column in RUBRIC_COLUMNS:
            batch_op.drop_constraint(
                f"ck_retrieval_qa_review_{column}_range",
                type_="check",
            )
        for column in (*RUBRIC_COLUMNS, "notes"):
            batch_op.drop_column(column)


def _canonical_review_values(row: Any) -> dict[str, Any]:
    checks = _json_object(row["checks"])
    nested_values = checks.get("values")
    if not isinstance(nested_values, dict):
        nested_values = {}

    scores = {
        column: _score(checks.get(column, nested_values.get(column)))
        for column in RUBRIC_COLUMNS
    }
    notes = checks.get("notes")
    existing_verdict = str(row["verdict"]) if row["verdict"] is not None else None
    accept_as_gold = _bool_or_none(checks.get("accept_as_gold"))
    skipped = bool(row["skipped"])
    skip_reason = _nonblank_string(row["skip_reason"])

    if skipped or existing_verdict == "NEEDS_REVIEW":
        return {
            **dict.fromkeys(RUBRIC_COLUMNS),
            "notes": notes,
            "verdict": None,
            "skipped": True,
            "skip_reason": skip_reason or "Migrated skipped retrieval review",
        }

    verdict = (
        "ACCEPT"
        if accept_as_gold is True
        else "REJECT"
        if accept_as_gold is False
        else existing_verdict
        if existing_verdict in {"ACCEPT", "REJECT"}
        else None
    )
    if verdict is None or any(value is None for value in scores.values()):
        return {
            **dict.fromkeys(RUBRIC_COLUMNS),
            "notes": notes,
            "verdict": None,
            "skipped": True,
            "skip_reason": skip_reason or "Migrated incomplete retrieval review",
        }
    return {
        **scores,
        "notes": notes,
        "verdict": verdict,
        "skipped": False,
        "skip_reason": None,
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    return score if score in {1, 2, 3, 4} else None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _nonblank_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
