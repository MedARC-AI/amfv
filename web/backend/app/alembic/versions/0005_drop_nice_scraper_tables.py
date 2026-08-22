"""Drop retired NICE scraper persistence after provenance verification.

Revision ID: 0005_drop_nice_scraper_tables
Revises: 0004_document_provenance
Create Date: 2026-08-22 03:00:00.000000

"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "0005_drop_nice_scraper_tables"
down_revision = "0004_document_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _assert_drop_preconditions(op.get_bind())
    op.drop_table("nice_import_item")
    op.drop_table("nice_import_job")
    op.drop_table("nice_download")


def downgrade() -> None:
    """Recreate empty legacy table shapes; dropped scraper cache data needs a backup restore."""
    _create_nice_download_table()
    _create_nice_import_job_table()
    _create_nice_import_item_table()


def _assert_drop_preconditions(connection: sa.Connection) -> None:
    active_jobs = (
        connection.execute(
            sa.text(
                """
            SELECT id FROM nice_import_job
            WHERE active_slot IS NOT NULL OR status IN ('pending', 'running')
            """
            )
        )
        .scalars()
        .all()
    )
    if active_jobs:
        raise RuntimeError(
            "Cannot drop NICE scraper tables while an import job is active"
        )

    cache_without_document = (
        connection.execute(
            sa.text(
                """
            SELECT nice_download.reference
            FROM nice_download
            LEFT JOIN document
              ON document.external_id = 'nice-' || nice_download.slug
            WHERE document.id IS NULL
            """
            )
        )
        .scalars()
        .all()
    )
    if cache_without_document:
        raise RuntimeError(
            "Cannot drop NICE scraper tables while cached guidance lacks a materialized document"
        )

    rows = (
        connection.execute(
            sa.text(
                """
            SELECT document.id, document.content, document.source, document.source_url,
                   document.source_metadata, document.source_content_hash,
                   nice_download.reference, nice_download.slug,
                   nice_download.page_url, nice_download.content AS cached_content
            FROM document
            JOIN nice_download
              ON document.external_id = 'nice-' || nice_download.slug
            ORDER BY document.id
            """
            )
        )
        .mappings()
        .all()
    )
    matched_ids = [int(row["id"]) for row in rows]
    if len(matched_ids) != len(set(matched_ids)):
        raise RuntimeError(
            "Cannot drop NICE scraper tables: one document matched multiple cache rows"
        )
    for row in rows:
        document_id = int(row["id"])
        content = str(row["content"])
        cached_content = str(row["cached_content"])
        expected_hash = _content_hash(content)
        metadata = _json_object(row["source_metadata"], document_id=document_id)
        if (
            row["source"] != "nice"
            or content != cached_content
            or row["source_url"] != row["page_url"]
            or row["source_content_hash"] != expected_hash
            or _content_hash(cached_content) != expected_hash
            or metadata.get("legacy_cache") is not True
            or metadata.get("ref") != row["reference"]
            or metadata.get("slug") != row["slug"]
        ):
            raise RuntimeError(
                f"Cannot drop NICE scraper tables: cache/provenance evidence failed for document {document_id}"
            )


def _create_nice_download_table() -> None:
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
    op.create_index(
        "ix_nice_download_reference", "nice_download", ["reference"], unique=True
    )
    op.create_index(
        "ix_nice_download_requested_by_user_id",
        "nice_download",
        ["requested_by_user_id"],
    )


def _create_nice_import_job_table() -> None:
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
    op.create_index(
        "ix_nice_import_job_active_slot",
        "nice_import_job",
        ["active_slot"],
        unique=True,
    )
    op.create_index("ix_nice_import_job_status", "nice_import_job", ["status"])
    op.create_index(
        "ix_nice_import_job_lease_expires_at", "nice_import_job", ["lease_expires_at"]
    )
    op.create_index(
        "ix_nice_import_job_started_by_user_id",
        "nice_import_job",
        ["started_by_user_id"],
    )


def _create_nice_import_item_table() -> None:
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
        sa.UniqueConstraint(
            "job_id", "reference", name="uq_nice_import_item_job_reference"
        ),
    )
    op.create_index(
        "ix_nice_import_item_pending",
        "nice_import_item",
        ["job_id", "status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_index("ix_nice_import_item_job_id", "nice_import_item", ["job_id"])
    op.create_index(
        "ix_nice_import_item_lease_expires_at", "nice_import_item", ["lease_expires_at"]
    )
    op.create_index(
        "ix_nice_import_item_next_attempt_at", "nice_import_item", ["next_attempt_at"]
    )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _json_object(value: object, *, document_id: int) -> dict:
    """Normalize SQLite JSON text and PostgreSQL JSON values to an object."""
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Cannot drop NICE scraper tables: document {document_id} has invalid provenance metadata"
            ) from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(
            f"Cannot drop NICE scraper tables: document {document_id} has non-object provenance metadata"
        )
    return decoded


def _str(length: int | None = None) -> sqlmodel.sql.sqltypes.AutoString:
    return sqlmodel.sql.sqltypes.AutoString(length=length)
