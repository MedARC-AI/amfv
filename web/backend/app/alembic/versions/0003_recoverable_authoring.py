"""Add durable retrieval-batch receipts for recoverable authoring.

Revision ID: 0003_recoverable_authoring
Revises: 0002_canonical_review_assignment_state
Create Date: 2026-08-22 01:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_recoverable_authoring"
down_revision = "0002_canonical_review_assignment_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_submission_batch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_item_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_user_id"], ["user.id"]),
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
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retrieval_submission_batch_author_user_id",
        table_name="retrieval_submission_batch",
    )
    op.drop_table("retrieval_submission_batch")
