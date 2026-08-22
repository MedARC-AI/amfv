"""Add fact-save receipts and enforce one assignment terminal state.

Revision ID: 0006_fact_save_receipts
Revises: 0005_drop_nice_scraper_tables
Create Date: 2026-08-22 04:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_fact_save_receipts"
down_revision = "0005_drop_nice_scraper_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    invalid_assignment = bind.execute(
        sa.text(
            """
            SELECT id
            FROM assignment
            WHERE completed_at IS NOT NULL AND released_at IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if invalid_assignment is not None:
        raise RuntimeError(
            "Cannot enforce assignment terminal state while an assignment is both completed and released"
        )

    with op.batch_alter_table("assignment") as batch_op:
        batch_op.create_check_constraint(
            "ck_assignment_single_terminal_state",
            "completed_at IS NULL OR released_at IS NULL",
        )

    op.create_table(
        "fact_decomp_save_receipt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("command", sa.String(length=16), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["eval_item.id"]),
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
    )
    op.create_index(
        "ix_fact_decomp_save_receipt_item_id",
        "fact_decomp_save_receipt",
        ["item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fact_decomp_save_receipt_item_id",
        table_name="fact_decomp_save_receipt",
    )
    op.drop_index(
        "ix_fact_decomp_save_receipt_author_user_id",
        table_name="fact_decomp_save_receipt",
    )
    op.drop_table("fact_decomp_save_receipt")

    with op.batch_alter_table("assignment") as batch_op:
        batch_op.drop_constraint(
            "ck_assignment_single_terminal_state",
            type_="check",
        )
