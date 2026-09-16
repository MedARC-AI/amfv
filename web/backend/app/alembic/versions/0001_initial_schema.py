"""Create the consolidated application schema.

Revision ID: 0001_initial_schema
Revises:

Replaces the development migration chain through 0007_drop_review_checks.
This baseline is for new databases.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("name", sa.VARCHAR(), nullable=False),
        sa.Column("display_name", sa.VARCHAR(), nullable=False),
        sa.Column("description", sa.VARCHAR(), nullable=True),
        sa.Column("eval_type", sa.VARCHAR(length=32), nullable=False),
        sa.Column("subtype_targets", sa.JSON(), nullable=True),
        sa.Column("category_definitions", sa.JSON(), nullable=True),
        sa.Column("double_rate", sa.FLOAT(), nullable=False),
        sa.Column("trap_rate", sa.FLOAT(), nullable=False),
        sa.Column("is_active", sa.BOOLEAN(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dataset_name", "dataset", ["name"], unique=True)
    op.create_table(
        "document",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("external_id", sa.VARCHAR(), nullable=False),
        sa.Column("title", sa.VARCHAR(), nullable=False),
        sa.Column("content", sa.VARCHAR(), nullable=False),
        sa.Column("paragraphs", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.BOOLEAN(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.Column("source", sa.VARCHAR(), nullable=True),
        sa.Column("source_url", sa.VARCHAR(), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=True),
        sa.Column("source_content_hash", sa.VARCHAR(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id", "external_id", name="uq_document_dataset_external"
        ),
    )
    op.create_index("ix_document_dataset_id", "document", ["dataset_id"], unique=False)
    op.create_index(
        "ix_document_external_id", "document", ["external_id"], unique=False
    )
    op.create_table(
        "user",
        sa.Column("email", sa.VARCHAR(length=255), nullable=False),
        sa.Column("is_active", sa.BOOLEAN(), nullable=False),
        sa.Column("is_superuser", sa.BOOLEAN(), nullable=False),
        sa.Column("full_name", sa.VARCHAR(length=255), nullable=True),
        sa.Column("discord_handle", sa.VARCHAR(length=255), nullable=True),
        sa.Column("medical_profession", sa.VARCHAR(length=100), nullable=True),
        sa.Column(
            "role",
            sa.VARCHAR(length=32),
            server_default=sa.text("'user'"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_kind",
            sa.VARCHAR(length=32),
            server_default=sa.text("'human'"),
            nullable=False,
        ),
        sa.Column("expertise_note", sa.VARCHAR(length=500), nullable=True),
        sa.Column("id", sa.CHAR(length=32), nullable=False),
        sa.Column("hashed_password", sa.VARCHAR(), nullable=False),
        sa.Column(
            "dataset_streak_remaining",
            sa.INTEGER(),
            server_default=sa.text("'0'"),
            nullable=False,
        ),
        sa.Column("display_name", sa.VARCHAR(length=255), nullable=True),
        sa.Column(
            "label_count_session",
            sa.INTEGER(),
            server_default=sa.text("'0'"),
            nullable=False,
        ),
        sa.Column(
            "label_count_total",
            sa.INTEGER(),
            server_default=sa.text("'0'"),
            nullable=False,
        ),
        sa.Column(
            "created_item_count",
            sa.INTEGER(),
            server_default=sa.text("'0'"),
            nullable=False,
        ),
        sa.Column(
            "items_authored_total",
            sa.INTEGER(),
            server_default=sa.text("'0'"),
            nullable=False,
        ),
        sa.Column(
            "profile_completed",
            sa.BOOLEAN(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("llm_model", sa.VARCHAR(length=255), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=True),
        sa.Column("updated_at", sa.DATETIME(), nullable=True),
        sa.Column("retrieval_dataset_id", sa.INTEGER(), nullable=True),
        sa.Column("fact_decomp_dataset_id", sa.INTEGER(), nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_decomp_dataset_id"],
            ["dataset.id"],
            name="fk_user_fact_decomp_dataset_id_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_dataset_id"],
            ["dataset.id"],
            name="fk_user_retrieval_dataset_id_dataset",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)
    op.create_index(
        "ix_user_fact_decomp_dataset_id",
        "user",
        ["fact_decomp_dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_retrieval_dataset_id", "user", ["retrieval_dataset_id"], unique=False
    )
    op.create_table(
        "adjudication",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("mode", sa.VARCHAR(length=32), nullable=False),
        sa.Column("target_id", sa.INTEGER(), nullable=False),
        sa.Column("judgment_ids", sa.JSON(), nullable=False),
        sa.Column("adjudicator_user_id", sa.CHAR(length=32), nullable=True),
        sa.Column("final", sa.JSON(), nullable=True),
        sa.Column("resolved_at", sa.DATETIME(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["adjudicator_user_id"],
            ["user.id"],
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mode", "target_id", "resolved_at", name="uq_open_adjudication_mode_target"
        ),
    )
    op.create_index(
        "ix_adjudication_adjudicator_user_id",
        "adjudication",
        ["adjudicator_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_adjudication_dataset_mode",
        "adjudication",
        ["dataset_id", "mode", "resolved_at"],
        unique=False,
    )
    op.create_index(
        "ix_adjudication_target_id", "adjudication", ["target_id"], unique=False
    )
    op.create_table(
        "assignment",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("mode", sa.VARCHAR(length=32), nullable=False),
        sa.Column("target_id", sa.INTEGER(), nullable=False),
        sa.Column("user_id", sa.CHAR(length=32), nullable=False),
        sa.Column("kind", sa.VARCHAR(length=32), nullable=False),
        sa.Column("assigned_at", sa.DATETIME(), nullable=False),
        sa.Column("completed_at", sa.DATETIME(), nullable=True),
        sa.Column("position", sa.INTEGER(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.Column("slot", sa.INTEGER(), nullable=False),
        sa.Column("released_at", sa.DATETIME(), nullable=True),
        sa.Column("release_reason", sa.VARCHAR(length=500), nullable=True),
        sa.CheckConstraint(
            "completed_at IS NULL OR released_at IS NULL",
            name="ck_assignment_single_terminal_state",
        ),
        sa.CheckConstraint("slot >= 0", name="ck_assignment_slot_nonnegative"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assignment_queue",
        "assignment",
        ["dataset_id", "mode", "completed_at", "position"],
        unique=False,
    )
    op.create_index(
        "ix_assignment_released_at", "assignment", ["released_at"], unique=False
    )
    op.create_index(
        "ix_assignment_target_id", "assignment", ["target_id"], unique=False
    )
    op.create_index("ix_assignment_user_id", "assignment", ["user_id"], unique=False)
    op.create_index(
        "uq_assignment_live_mode_target_slot",
        "assignment",
        ["mode", "target_id", "slot"],
        unique=True,
        sqlite_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "uq_assignment_live_mode_target_user",
        "assignment",
        ["mode", "target_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("released_at IS NULL"),
    )
    op.create_table(
        "calibration_status",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("user_id", sa.CHAR(length=32), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("completed_at", sa.DATETIME(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "dataset_id", name="uq_calibration_user_dataset"
        ),
    )
    op.create_index(
        "ix_calibration_status_dataset_id",
        "calibration_status",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_calibration_status_user_id", "calibration_status", ["user_id"], unique=False
    )
    op.create_table(
        "chunk",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("document_id", sa.INTEGER(), nullable=False),
        sa.Column("external_id", sa.VARCHAR(), nullable=False),
        sa.Column("text", sa.VARCHAR(), nullable=False),
        sa.Column("position", sa.INTEGER(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id", "external_id", name="uq_chunk_dataset_external"
        ),
    )
    op.create_index("ix_chunk_dataset_id", "chunk", ["dataset_id"], unique=False)
    op.create_index("ix_chunk_document_id", "chunk", ["document_id"], unique=False)
    op.create_index("ix_chunk_external_id", "chunk", ["external_id"], unique=False)
    op.create_table(
        "eval_item",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("external_id", sa.VARCHAR(), nullable=True),
        sa.Column("eval_type", sa.VARCHAR(length=32), nullable=False),
        sa.Column("category", sa.VARCHAR(length=32), nullable=True),
        sa.Column("document_id", sa.INTEGER(), nullable=True),
        sa.Column("source", sa.VARCHAR(length=32), nullable=False),
        sa.Column("author_kind", sa.VARCHAR(length=32), nullable=True),
        sa.Column("author_user_id", sa.CHAR(length=32), nullable=True),
        sa.Column("generator_name", sa.VARCHAR(), nullable=True),
        sa.Column("prompt_text", sa.VARCHAR(), nullable=False),
        sa.Column("lazy_query", sa.VARCHAR(), nullable=True),
        sa.Column("expected_answer", sa.VARCHAR(), nullable=True),
        sa.Column("evidence_spans", sa.JSON(), nullable=True),
        sa.Column("gold_chunk_ids", sa.JSON(), nullable=True),
        sa.Column("trap_chunk_ids", sa.JSON(), nullable=True),
        sa.Column("why_not_answerable", sa.VARCHAR(), nullable=True),
        sa.Column("machine_span", sa.JSON(), nullable=True),
        sa.Column("priority_tag", sa.VARCHAR(), nullable=True),
        sa.Column("is_calibration", sa.BOOLEAN(), nullable=False),
        sa.Column("calibration_reference", sa.JSON(), nullable=True),
        sa.Column("is_trap", sa.BOOLEAN(), nullable=False),
        sa.Column("trap_note", sa.VARCHAR(), nullable=True),
        sa.Column("flagged_ambiguous", sa.BOOLEAN(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("validation_flags", sa.JSON(), nullable=True),
        sa.Column("rejection_reason", sa.VARCHAR(), nullable=True),
        sa.Column("status", sa.VARCHAR(length=32), nullable=False),
        sa.Column("revision", sa.INTEGER(), nullable=False),
        sa.Column("is_active", sa.BOOLEAN(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["user.id"],
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id", "external_id", name="uq_eval_item_dataset_external"
        ),
    )
    op.create_index(
        "ix_eval_item_author_user_id", "eval_item", ["author_user_id"], unique=False
    )
    op.create_index(
        "ix_eval_item_dataset_id", "eval_item", ["dataset_id"], unique=False
    )
    op.create_index(
        "ix_eval_item_dataset_status_type",
        "eval_item",
        ["dataset_id", "status", "eval_type"],
        unique=False,
    )
    op.create_index(
        "ix_eval_item_document_id", "eval_item", ["document_id"], unique=False
    )
    op.create_index(
        "ix_eval_item_external_id", "eval_item", ["external_id"], unique=False
    )
    op.create_index(
        "ix_eval_item_priority_tag", "eval_item", ["priority_tag"], unique=False
    )
    op.create_table(
        "retrieval_submission_batch",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("author_user_id", sa.CHAR(length=32), nullable=False),
        sa.Column("request_id", sa.VARCHAR(length=128), nullable=False),
        sa.Column("request_hash", sa.VARCHAR(length=64), nullable=False),
        sa.Column("created_item_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "author_user_id",
            "request_id",
            name="uq_retrieval_submission_batch_author_request",
        ),
    )
    op.create_index(
        "ix_retrieval_submission_batch_author_user_id",
        "retrieval_submission_batch",
        ["author_user_id"],
        unique=False,
    )
    op.create_table(
        "signupinvite",
        sa.Column("role", sa.VARCHAR(length=32), nullable=False),
        sa.Column("expires_at", sa.DATETIME(), nullable=True),
        sa.Column("max_redemptions", sa.INTEGER(), nullable=False),
        sa.Column("id", sa.CHAR(length=32), nullable=False),
        sa.Column("token_hash", sa.VARCHAR(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.CHAR(length=32), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=True),
        sa.Column("redeemed_count", sa.INTEGER(), nullable=False),
        sa.Column("disabled_at", sa.DATETIME(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_signupinvite_token_hash", "signupinvite", ["token_hash"], unique=True
    )
    op.create_table(
        "eval_fact",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("item_id", sa.INTEGER(), nullable=False),
        sa.Column("fact_text", sa.VARCHAR(), nullable=False),
        sa.Column("polarity", sa.VARCHAR(length=32), nullable=False),
        sa.Column("position", sa.INTEGER(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["eval_item.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "position", name="uq_eval_fact_item_position"),
    )
    op.create_index("ix_eval_fact_item_id", "eval_fact", ["item_id"], unique=False)
    op.create_table(
        "fact_decomp_save_receipt",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("author_user_id", sa.CHAR(length=32), nullable=False),
        sa.Column("request_id", sa.VARCHAR(length=128), nullable=False),
        sa.Column("request_hash", sa.VARCHAR(length=64), nullable=False),
        sa.Column("command", sa.VARCHAR(length=16), nullable=False),
        sa.Column("item_id", sa.INTEGER(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["user.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["eval_item.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "author_user_id",
            "request_id",
            name="uq_fact_decomp_save_receipt_author_request",
        ),
    )
    op.create_index(
        "ix_fact_decomp_save_receipt_author_user_id",
        "fact_decomp_save_receipt",
        ["author_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_fact_decomp_save_receipt_item_id",
        "fact_decomp_save_receipt",
        ["item_id"],
        unique=False,
    )
    op.create_table(
        "pooled_candidate",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("item_id", sa.INTEGER(), nullable=False),
        sa.Column("chunk_id", sa.INTEGER(), nullable=False),
        sa.Column("systems", sa.JSON(), nullable=False),
        sa.Column("ranks", sa.JSON(), nullable=False),
        sa.Column("is_calibration", sa.BOOLEAN(), nullable=False),
        sa.Column("is_trap", sa.BOOLEAN(), nullable=False),
        sa.Column("reference_grade", sa.INTEGER(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunk.id"],
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["eval_item.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id", "chunk_id", name="uq_pooled_candidate_item_chunk"
        ),
    )
    op.create_index(
        "ix_pooled_candidate_chunk_id", "pooled_candidate", ["chunk_id"], unique=False
    )
    op.create_index(
        "ix_pooled_candidate_dataset_item",
        "pooled_candidate",
        ["dataset_id", "item_id"],
        unique=False,
    )
    op.create_table(
        "retrieval_qa_review",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("assignment_id", sa.INTEGER(), nullable=False),
        sa.Column("item_id", sa.INTEGER(), nullable=False),
        sa.Column("user_id", sa.CHAR(length=32), nullable=False),
        sa.Column("span", sa.JSON(), nullable=True),
        sa.Column("span_overlap", sa.FLOAT(), nullable=True),
        sa.Column("confidence", sa.VARCHAR(length=32), nullable=True),
        sa.Column("verdict", sa.VARCHAR(length=32), nullable=True),
        sa.Column("skipped", sa.BOOLEAN(), nullable=False),
        sa.Column("skip_reason", sa.VARCHAR(), nullable=True),
        sa.Column("started_at", sa.DATETIME(), nullable=False),
        sa.Column("submitted_at", sa.DATETIME(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.Column("question_validity", sa.INTEGER(), nullable=True),
        sa.Column("evidence_quality", sa.INTEGER(), nullable=True),
        sa.Column("answer_correctness", sa.INTEGER(), nullable=True),
        sa.Column("answer_faithfulness", sa.INTEGER(), nullable=True),
        sa.Column("notes", sa.TEXT(), nullable=True),
        sa.CheckConstraint(
            "(skipped = TRUE AND skip_reason IS NOT NULL AND length(trim(skip_reason)) > 0 AND question_validity IS NULL AND evidence_quality IS NULL AND answer_correctness IS NULL AND answer_faithfulness IS NULL AND verdict IS NULL) OR (skipped = FALSE AND skip_reason IS NULL AND question_validity IS NOT NULL AND question_validity BETWEEN 1 AND 4 AND evidence_quality IS NOT NULL AND evidence_quality BETWEEN 1 AND 4 AND answer_correctness IS NOT NULL AND answer_correctness BETWEEN 1 AND 4 AND answer_faithfulness IS NOT NULL AND answer_faithfulness BETWEEN 1 AND 4 AND verdict IS NOT NULL AND verdict IN ('ACCEPT', 'REJECT'))",
            name="ck_retrieval_qa_review_submission_shape",
        ),
        sa.CheckConstraint(
            "answer_correctness IS NULL OR answer_correctness BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_answer_correctness_range",
        ),
        sa.CheckConstraint(
            "answer_faithfulness IS NULL OR answer_faithfulness BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_answer_faithfulness_range",
        ),
        sa.CheckConstraint(
            "evidence_quality IS NULL OR evidence_quality BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_evidence_quality_range",
        ),
        sa.CheckConstraint(
            "question_validity IS NULL OR question_validity BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_question_validity_range",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["assignment.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["eval_item.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", name="uq_retrieval_qa_review_assignment"),
    )
    op.create_index(
        "ix_retrieval_qa_review_assignment_id",
        "retrieval_qa_review",
        ["assignment_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_qa_review_item_id",
        "retrieval_qa_review",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_qa_review_user_id",
        "retrieval_qa_review",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "review_task",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("dataset_id", sa.INTEGER(), nullable=False),
        sa.Column("item_a_id", sa.INTEGER(), nullable=False),
        sa.Column("is_gold", sa.BOOLEAN(), nullable=False),
        sa.Column("priority_score", sa.FLOAT(), nullable=False),
        sa.Column("labels_count", sa.INTEGER(), nullable=False),
        sa.Column("is_active", sa.BOOLEAN(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_a_id"],
            ["eval_item.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_a_id", name="uq_review_task_item"),
    )
    op.create_index(
        "ix_review_task_dataset_id", "review_task", ["dataset_id"], unique=False
    )
    op.create_index(
        "ix_review_task_item_a_id", "review_task", ["item_a_id"], unique=False
    )
    op.create_index(
        "ix_review_task_priority",
        "review_task",
        ["dataset_id", "is_active", "labels_count", "priority_score"],
        unique=False,
    )
    op.create_table(
        "fact_decomp_review",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("task_id", sa.INTEGER(), nullable=False),
        sa.Column("user_id", sa.CHAR(length=32), nullable=False),
        sa.Column("item_revision", sa.INTEGER(), nullable=False),
        sa.Column("ratings", sa.JSON(), nullable=True),
        sa.Column("reviewer_kind", sa.VARCHAR(length=32), nullable=False),
        sa.Column("comment", sa.VARCHAR(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column("source", sa.VARCHAR(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["review_task.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "user_id", name="uq_fact_decomp_review_task_user"
        ),
    )
    op.create_index(
        "ix_fact_decomp_review_task_id", "fact_decomp_review", ["task_id"], unique=False
    )
    op.create_index(
        "ix_fact_decomp_review_user_id", "fact_decomp_review", ["user_id"], unique=False
    )
    op.create_table(
        "relevance_judgment",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("assignment_id", sa.INTEGER(), nullable=False),
        sa.Column("candidate_id", sa.INTEGER(), nullable=False),
        sa.Column("user_id", sa.CHAR(length=32), nullable=False),
        sa.Column("grade", sa.INTEGER(), nullable=True),
        sa.Column("confidence", sa.VARCHAR(length=32), nullable=True),
        sa.Column("skipped", sa.BOOLEAN(), nullable=False),
        sa.Column("skip_reason", sa.VARCHAR(), nullable=True),
        sa.Column("started_at", sa.DATETIME(), nullable=False),
        sa.Column("submitted_at", sa.DATETIME(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("updated_at", sa.DATETIME(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["assignment.id"],
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["pooled_candidate.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", name="uq_relevance_judgment_assignment"),
    )
    op.create_index(
        "ix_relevance_judgment_assignment_id",
        "relevance_judgment",
        ["assignment_id"],
        unique=False,
    )
    op.create_index(
        "ix_relevance_judgment_candidate_id",
        "relevance_judgment",
        ["candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_relevance_judgment_user_id", "relevance_judgment", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("relevance_judgment")
    op.drop_table("fact_decomp_review")
    op.drop_table("review_task")
    op.drop_table("retrieval_qa_review")
    op.drop_table("pooled_candidate")
    op.drop_table("fact_decomp_save_receipt")
    op.drop_table("eval_fact")
    op.drop_table("signupinvite")
    op.drop_table("retrieval_submission_batch")
    op.drop_table("eval_item")
    op.drop_table("chunk")
    op.drop_table("calibration_status")
    op.drop_table("assignment")
    op.drop_table("adjudication")
    op.drop_table("user")
    op.drop_table("document")
    op.drop_table("dataset")
