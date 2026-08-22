"""Add generic source provenance and backfill materialized NICE documents.

Revision ID: 0004_document_provenance
Revises: 0003_recoverable_authoring
Create Date: 2026-08-22 02:00:00.000000

"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0004_document_provenance"
down_revision = "0003_recoverable_authoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("source_url", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("source_metadata", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("source_content_hash", sa.String(length=64), nullable=True)
        )
    _backfill_nice_document_provenance(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("document") as batch_op:
        batch_op.drop_column("source_content_hash")
        batch_op.drop_column("source_metadata")
        batch_op.drop_column("source_url")
        batch_op.drop_column("source")


def _backfill_nice_document_provenance(connection: sa.Connection) -> None:
    rows = (
        connection.execute(
            sa.text(
                """
            SELECT document.id, document.content, nice_download.reference,
                   nice_download.slug, nice_download.page_url,
                   nice_download.content AS cached_content
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
    document_ids = [int(row["id"]) for row in rows]
    if len(document_ids) != len(set(document_ids)):
        raise RuntimeError(
            "Cannot backfill NICE provenance: one document matched multiple cache rows"
        )

    expected: dict[int, dict[str, Any]] = {}
    document_table = sa.table(
        "document",
        sa.column("id", sa.Integer()),
        sa.column("source", sa.String()),
        sa.column("source_url", sa.String()),
        sa.column("source_metadata", sa.JSON()),
        sa.column("source_content_hash", sa.String(length=64)),
    )
    for row in rows:
        document_id = int(row["id"])
        content = str(row["content"])
        cached_content = str(row["cached_content"])
        if content != cached_content:
            raise RuntimeError(
                f"Cannot backfill NICE provenance: document {document_id} differs from its cached source text"
            )
        content_hash = _content_hash(content)
        expected[document_id] = {
            "content": content,
            "source_url": str(row["page_url"]),
            "source_metadata": {
                "legacy_cache": True,
                "ref": str(row["reference"]),
                "slug": str(row["slug"]),
            },
            "source_content_hash": content_hash,
        }
        connection.execute(
            sa.update(document_table)
            .where(document_table.c.id == document_id)
            .values(
                source="nice",
                source_url=expected[document_id]["source_url"],
                source_metadata=expected[document_id]["source_metadata"],
                source_content_hash=content_hash,
            )
        )

    verified = (
        connection.execute(
            sa.text(
                """
            SELECT id, content, source, source_url, source_metadata, source_content_hash
            FROM document
            WHERE id IN :document_ids
            ORDER BY id
            """
            ).bindparams(sa.bindparam("document_ids", expanding=True)),
            {"document_ids": document_ids},
        )
        .mappings()
        .all()
        if document_ids
        else []
    )
    if len(verified) != len(expected):
        raise RuntimeError(
            "NICE provenance backfill count did not match the legacy materialization count"
        )
    for row in verified:
        document_id = int(row["id"])
        evidence = expected[document_id]
        metadata = _json_object(row["source_metadata"], document_id=document_id)
        if (
            row["source"] != "nice"
            or row["source_url"] != evidence["source_url"]
            or metadata != evidence["source_metadata"]
            or row["source_content_hash"] != evidence["source_content_hash"]
            or str(row["content"]) != evidence["content"]
            or _content_hash(str(row["content"])) != evidence["source_content_hash"]
        ):
            raise RuntimeError(
                f"NICE provenance backfill verification failed for document {document_id}"
            )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _json_object(value: object, *, document_id: int) -> dict[str, Any]:
    """Normalize SQLite JSON text and PostgreSQL JSON values to an object."""
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"NICE provenance backfill produced invalid metadata for document {document_id}"
            ) from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(
            f"NICE provenance backfill produced non-object metadata for document {document_id}"
        )
    return decoded
