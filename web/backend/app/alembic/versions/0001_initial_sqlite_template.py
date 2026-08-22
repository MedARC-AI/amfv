"""Initial application schema

Revision ID: 0001_initial_sqlite_template
Revises:
Create Date: 2026-06-18 00:00:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op


revision = "0001_initial_sqlite_template"
down_revision = None
branch_labels = None
depends_on = None


def _str(length: int | None = None):
    return sqlmodel.sql.sqltypes.AutoString(length=length)


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "user",
        sa.Column("email", _str(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("full_name", _str(255), nullable=True),
        sa.Column("discord_handle", _str(255), nullable=True),
        sa.Column("medical_profession", _str(100), nullable=True),
        sa.Column("role", _str(32), nullable=False, server_default="user"),
        sa.Column("reviewer_kind", _str(32), nullable=False, server_default="human"),
        sa.Column("expertise_note", _str(500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hashed_password", _str(), nullable=False),
        sa.Column("current_dataset_id", sa.Integer(), nullable=True),
        sa.Column("dataset_streak_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("display_name", _str(255), nullable=True),
        sa.Column("label_count_session", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label_count_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_authored_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profile_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("llm_model", _str(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)
    op.create_index("ix_user_current_dataset_id", "user", ["current_dataset_id"])

    op.create_table(
        "signupinvite",
        sa.Column("role", _str(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", _str(64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_count", sa.Integer(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_signupinvite_token_hash"), "signupinvite", ["token_hash"], unique=True)

    op.create_table(
        "dataset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", _str(), nullable=False),
        sa.Column("display_name", _str(), nullable=False),
        sa.Column("description", _str(), nullable=True),
        sa.Column("eval_type", _str(32), nullable=False),
        sa.Column("subtype_targets", sa.JSON(), nullable=True),
        sa.Column("category_definitions", sa.JSON(), nullable=True),
        sa.Column("double_rate", sa.Float(), nullable=False),
        sa.Column("trap_rate", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dataset_name"), "dataset", ["name"], unique=True)

    with op.batch_alter_table("user") as batch_op:
        batch_op.create_foreign_key("fk_user_current_dataset_id_dataset", "dataset", ["current_dataset_id"], ["id"])

    op.create_table(
        "document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("external_id", _str(), nullable=False),
        sa.Column("title", _str(), nullable=False),
        sa.Column("content", _str(), nullable=False),
        sa.Column("paragraphs", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "external_id", name="uq_document_dataset_external"),
    )
    op.create_index(op.f("ix_document_dataset_id"), "document", ["dataset_id"])
    op.create_index(op.f("ix_document_external_id"), "document", ["external_id"])

    op.create_table(
        "chunk",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("external_id", _str(), nullable=False),
        sa.Column("text", _str(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "external_id", name="uq_chunk_dataset_external"),
    )
    op.create_index(op.f("ix_chunk_dataset_id"), "chunk", ["dataset_id"])
    op.create_index(op.f("ix_chunk_document_id"), "chunk", ["document_id"])
    op.create_index(op.f("ix_chunk_external_id"), "chunk", ["external_id"])

    op.create_table(
        "eval_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("external_id", _str(), nullable=True),
        sa.Column("eval_type", _str(32), nullable=False),
        sa.Column("category", _str(32), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("source", _str(32), nullable=False),
        sa.Column("author_kind", _str(32), nullable=True),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("generator_name", _str(), nullable=True),
        sa.Column("prompt_text", _str(), nullable=False),
        sa.Column("lazy_query", _str(), nullable=True),
        sa.Column("expected_answer", _str(), nullable=True),
        sa.Column("evidence_spans", sa.JSON(), nullable=True),
        sa.Column("gold_chunk_ids", sa.JSON(), nullable=True),
        sa.Column("trap_chunk_ids", sa.JSON(), nullable=True),
        sa.Column("why_not_answerable", _str(), nullable=True),
        sa.Column("machine_span", sa.JSON(), nullable=True),
        sa.Column("priority_tag", _str(), nullable=True),
        sa.Column("is_calibration", sa.Boolean(), nullable=False),
        sa.Column("calibration_reference", sa.JSON(), nullable=True),
        sa.Column("is_trap", sa.Boolean(), nullable=False),
        sa.Column("trap_note", _str(), nullable=True),
        sa.Column("flagged_ambiguous", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("validation_flags", sa.JSON(), nullable=True),
        sa.Column("rejection_reason", _str(), nullable=True),
        sa.Column("status", _str(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["author_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "external_id", name="uq_eval_item_dataset_external"),
    )
    op.create_index("ix_eval_item_dataset_status_type", "eval_item", ["dataset_id", "status", "eval_type"])
    op.create_index(op.f("ix_eval_item_author_user_id"), "eval_item", ["author_user_id"])
    op.create_index(op.f("ix_eval_item_dataset_id"), "eval_item", ["dataset_id"])
    op.create_index(op.f("ix_eval_item_document_id"), "eval_item", ["document_id"])
    op.create_index(op.f("ix_eval_item_external_id"), "eval_item", ["external_id"])
    op.create_index(op.f("ix_eval_item_priority_tag"), "eval_item", ["priority_tag"])

    op.create_table(
        "eval_fact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("fact_uuid", _str(), nullable=False),
        sa.Column("fact_text", _str(), nullable=False),
        sa.Column("polarity", _str(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["item_id"], ["eval_item.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_eval_fact_fact_uuid"), "eval_fact", ["fact_uuid"])
    op.create_index(op.f("ix_eval_fact_item_id"), "eval_fact", ["item_id"])

    op.create_table(
        "review_task",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("item_a_id", sa.Integer(), nullable=False),
        sa.Column("is_gold", sa.Boolean(), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column("labels_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.ForeignKeyConstraint(["item_a_id"], ["eval_item.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_a_id", name="uq_review_task_item"),
    )
    op.create_index("ix_review_task_priority", "review_task", ["dataset_id", "is_active", "labels_count", "priority_score"])
    op.create_index(op.f("ix_review_task_dataset_id"), "review_task", ["dataset_id"])
    op.create_index(op.f("ix_review_task_item_a_id"), "review_task", ["item_a_id"])

    op.create_table(
        "fact_decomp_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_revision", sa.Integer(), nullable=False),
        sa.Column("ratings", sa.JSON(), nullable=True),
        sa.Column("reviewer_kind", _str(32), nullable=False),
        sa.Column("comment", _str(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column("source", _str(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["task_id"], ["review_task.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "user_id", name="uq_fact_decomp_review_task_user"),
    )
    op.create_index(op.f("ix_fact_decomp_review_task_id"), "fact_decomp_review", ["task_id"])
    op.create_index(op.f("ix_fact_decomp_review_user_id"), "fact_decomp_review", ["user_id"])

    op.create_table(
        "pooled_candidate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("systems", sa.JSON(), nullable=False),
        sa.Column("ranks", sa.JSON(), nullable=False),
        sa.Column("is_calibration", sa.Boolean(), nullable=False),
        sa.Column("is_trap", sa.Boolean(), nullable=False),
        sa.Column("reference_grade", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunk.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["eval_item.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "chunk_id", name="uq_pooled_candidate_item_chunk"),
    )
    op.create_index("ix_pooled_candidate_dataset_item", "pooled_candidate", ["dataset_id", "item_id"])
    op.create_index(op.f("ix_pooled_candidate_chunk_id"), "pooled_candidate", ["chunk_id"])

    op.create_table(
        "assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("mode", _str(32), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", _str(32), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mode", "target_id", "user_id", name="uq_assignment_mode_target_user"),
    )
    op.create_index("ix_assignment_queue", "assignment", ["dataset_id", "mode", "completed_at", "position"])
    op.create_index(op.f("ix_assignment_target_id"), "assignment", ["target_id"])
    op.create_index(op.f("ix_assignment_user_id"), "assignment", ["user_id"])

    op.create_table(
        "calibration_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "dataset_id", name="uq_calibration_user_dataset"),
    )
    op.create_index(op.f("ix_calibration_status_dataset_id"), "calibration_status", ["dataset_id"])
    op.create_index(op.f("ix_calibration_status_user_id"), "calibration_status", ["user_id"])

    op.create_table(
        "retrieval_qa_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("span", sa.JSON(), nullable=True),
        sa.Column("span_overlap", sa.Float(), nullable=True),
        sa.Column("confidence", _str(32), nullable=True),
        sa.Column("verdict", _str(32), nullable=True),
        sa.Column("skipped", sa.Boolean(), nullable=False),
        sa.Column("skip_reason", _str(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignment.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["eval_item.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", name="uq_retrieval_qa_review_assignment"),
    )
    op.create_index(op.f("ix_retrieval_qa_review_assignment_id"), "retrieval_qa_review", ["assignment_id"])
    op.create_index(op.f("ix_retrieval_qa_review_item_id"), "retrieval_qa_review", ["item_id"])
    op.create_index(op.f("ix_retrieval_qa_review_user_id"), "retrieval_qa_review", ["user_id"])

    op.create_table(
        "relevance_judgment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=True),
        sa.Column("confidence", _str(32), nullable=True),
        sa.Column("skipped", sa.Boolean(), nullable=False),
        sa.Column("skip_reason", _str(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignment.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["pooled_candidate.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", name="uq_relevance_judgment_assignment"),
    )
    op.create_index(op.f("ix_relevance_judgment_assignment_id"), "relevance_judgment", ["assignment_id"])
    op.create_index(op.f("ix_relevance_judgment_candidate_id"), "relevance_judgment", ["candidate_id"])
    op.create_index(op.f("ix_relevance_judgment_user_id"), "relevance_judgment", ["user_id"])

    op.create_table(
        "adjudication",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("mode", _str(32), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("judgment_ids", sa.JSON(), nullable=False),
        sa.Column("adjudicator_user_id", sa.Uuid(), nullable=True),
        sa.Column("final", sa.JSON(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["adjudicator_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mode", "target_id", "resolved_at", name="uq_open_adjudication_mode_target"),
    )
    op.create_index("ix_adjudication_dataset_mode", "adjudication", ["dataset_id", "mode", "resolved_at"])
    op.create_index(op.f("ix_adjudication_adjudicator_user_id"), "adjudication", ["adjudicator_user_id"])
    op.create_index(op.f("ix_adjudication_target_id"), "adjudication", ["target_id"])

    op.create_table(
        "nice_download",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reference", _str(), nullable=False),
        sa.Column("slug", _str(), nullable=False),
        sa.Column("title", _str(), nullable=False),
        sa.Column("page_url", _str(), nullable=False),
        sa.Column("pdf_url", _str(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("content", _str(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_nice_download_reference"), "nice_download", ["reference"], unique=True)
    op.create_index(op.f("ix_nice_download_requested_by_user_id"), "nice_download", ["requested_by_user_id"])

    op.create_table(
        "nice_import_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", _str(32), nullable=False),
        sa.Column("requested_limit", _str(16), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=True),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("started_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", _str(), nullable=True),
        sa.Column("lease_owner", _str(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["started_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nice_import_job_active_slot", "nice_import_job", ["active_slot"], unique=True)
    op.create_index("ix_nice_import_job_status", "nice_import_job", ["status"])
    op.create_index(op.f("ix_nice_import_job_lease_expires_at"), "nice_import_job", ["lease_expires_at"])
    op.create_index(op.f("ix_nice_import_job_started_by_user_id"), "nice_import_job", ["started_by_user_id"])

    op.create_table(
        "nice_import_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("reference", _str(32), nullable=False),
        sa.Column("slug", _str(), nullable=False),
        sa.Column("title", _str(), nullable=False),
        sa.Column("page_url", _str(), nullable=False),
        sa.Column("status", _str(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", _str(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["nice_import_job.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "reference", name="uq_nice_import_item_job_reference"),
    )
    op.create_index("ix_nice_import_item_pending", "nice_import_item", ["job_id", "status", "next_attempt_at", "lease_expires_at"])
    op.create_index(op.f("ix_nice_import_item_job_id"), "nice_import_item", ["job_id"])
    op.create_index(op.f("ix_nice_import_item_lease_expires_at"), "nice_import_item", ["lease_expires_at"])
    op.create_index(op.f("ix_nice_import_item_next_attempt_at"), "nice_import_item", ["next_attempt_at"])


def downgrade():
    op.drop_index(op.f("ix_nice_import_item_next_attempt_at"), table_name="nice_import_item")
    op.drop_index(op.f("ix_nice_import_item_lease_expires_at"), table_name="nice_import_item")
    op.drop_index(op.f("ix_nice_import_item_job_id"), table_name="nice_import_item")
    op.drop_index("ix_nice_import_item_pending", table_name="nice_import_item")
    op.drop_table("nice_import_item")
    op.drop_index(op.f("ix_nice_import_job_started_by_user_id"), table_name="nice_import_job")
    op.drop_index(op.f("ix_nice_import_job_lease_expires_at"), table_name="nice_import_job")
    op.drop_index("ix_nice_import_job_status", table_name="nice_import_job")
    op.drop_index("ix_nice_import_job_active_slot", table_name="nice_import_job")
    op.drop_table("nice_import_job")
    op.drop_index(op.f("ix_nice_download_requested_by_user_id"), table_name="nice_download")
    op.drop_index(op.f("ix_nice_download_reference"), table_name="nice_download")
    op.drop_table("nice_download")
    op.drop_table("adjudication")
    op.drop_table("relevance_judgment")
    op.drop_table("retrieval_qa_review")
    op.drop_table("calibration_status")
    op.drop_table("assignment")
    op.drop_table("pooled_candidate")
    op.drop_table("fact_decomp_review")
    op.drop_table("review_task")
    op.drop_table("eval_fact")
    op.drop_table("eval_item")
    op.drop_table("chunk")
    op.drop_table("document")
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("fk_user_current_dataset_id_dataset", type_="foreignkey")
    op.drop_index(op.f("ix_dataset_name"), table_name="dataset")
    op.drop_table("dataset")
    op.drop_index(op.f("ix_signupinvite_token_hash"), table_name="signupinvite")
    op.drop_table("signupinvite")
    op.drop_index("ix_user_current_dataset_id", table_name="user")
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_table("user")
