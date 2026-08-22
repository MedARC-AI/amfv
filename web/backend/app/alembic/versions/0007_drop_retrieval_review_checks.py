"""Remove the retired retrieval-review JSON shadow state.

Revision ID: 0007_drop_review_checks
Revises: 0006_fact_save_receipts
Create Date: 2026-08-22 05:00:00.000000

"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0007_drop_review_checks"
down_revision = "0006_fact_save_receipts"
branch_labels = None
depends_on = None

RUBRIC_COLUMNS = (
    "question_validity",
    "evidence_quality",
    "answer_correctness",
    "answer_faithfulness",
)


def upgrade() -> None:
    _assert_typed_equivalence(op.get_bind())
    with op.batch_alter_table("retrieval_qa_review") as batch_op:
        batch_op.drop_column("checks")


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("retrieval_qa_review") as batch_op:
        batch_op.add_column(sa.Column("checks", sa.JSON(), nullable=True))

    rows = bind.execute(
        sa.text(
            """
            SELECT id, question_validity, evidence_quality, answer_correctness,
                   answer_faithfulness, notes, verdict, skipped, skip_reason
            FROM retrieval_qa_review
            ORDER BY id
            """
        )
    ).mappings()
    for row in rows:
        checks = (
            {
                "skipped": True,
                "skip_reason": row["skip_reason"],
                "migrated_from_typed": True,
            }
            if bool(row["skipped"])
            else {
                **{column: row[column] for column in RUBRIC_COLUMNS},
                "accept_as_gold": row["verdict"] == "ACCEPT",
                "notes": row["notes"],
                "migrated_from_typed": True,
            }
        )
        bind.execute(
            sa.text("UPDATE retrieval_qa_review SET checks = :checks WHERE id = :id"),
            {"id": row["id"], "checks": json.dumps(checks)},
        )


def _assert_typed_equivalence(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            """
            SELECT id, checks, question_validity, evidence_quality,
                   answer_correctness, answer_faithfulness, notes, verdict,
                   skipped, skip_reason
            FROM retrieval_qa_review
            WHERE checks IS NOT NULL
            ORDER BY id
            """
        )
    ).mappings()
    for row in rows:
        checks = _json_object(row["checks"], review_id=int(row["id"]))
        if bool(row["skipped"]):
            if (
                any(row[column] is not None for column in RUBRIC_COLUMNS)
                or row["verdict"] is not None
            ):
                _refuse(row["id"])
            continue

        values = checks.get("values")
        if not isinstance(values, dict):
            values = {}
        for column in RUBRIC_COLUMNS:
            legacy = checks.get(column, values.get(column))
            if _score(legacy) != row[column]:
                _refuse(row["id"])
        if _bool_or_none(checks.get("accept_as_gold")) != (row["verdict"] == "ACCEPT"):
            _refuse(row["id"])
        if checks.get("notes") != row["notes"]:
            _refuse(row["id"])


def _json_object(value: Any, *, review_id: int) -> dict[str, Any]:
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Cannot drop retrieval review checks: review {review_id} has invalid JSON"
            ) from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(
            f"Cannot drop retrieval review checks: review {review_id} is not an object"
        )
    return decoded


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


def _refuse(review_id: Any) -> None:
    raise RuntimeError(
        f"Cannot drop retrieval review checks: review {review_id} disagrees with typed columns"
    )
