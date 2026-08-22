"""Migration evidence for source-document provenance and scraper retirement."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlmodel import Session

from app.core.config import settings
from app.models import Dataset
from app.services.document_import import (
    import_scraped_document,
    parse_scraped_document_row,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    BACKEND_ROOT.parent.parent
    / "datasets/test/fixtures/scraping/nice_document_v1.jsonl"
)
PRE_PROVENANCE_REVISION = "0003_recoverable_authoring"
PROVENANCE_REVISION = "0004_document_provenance"


def test_provenance_upgrade_and_retirement_round_trip_preserve_legacy_document_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "document-provenance.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    row = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    command.upgrade(config, PRE_PROVENANCE_REVISION)
    _insert_legacy_nice_materialization(database_url, row)

    command.upgrade(config, PROVENANCE_REVISION)
    _assert_provenance_backfill_evidence(database_url, row)

    backup_path = tmp_path / "document-provenance-backup.db"
    _backup_sqlite_database(database_path, backup_path)
    _tamper_post_backup_title(database_url)
    _restore_sqlite_database(backup_path, database_path)
    _assert_provenance_backfill_evidence(database_url, row)

    command.upgrade(config, "head")
    _assert_scraper_tables_absent_and_document_intact(database_url, row)
    _assert_canonical_fixture_reimport_is_unchanged(database_url, row)

    command.downgrade(config, PROVENANCE_REVISION)
    _assert_safe_downgrade_recreates_empty_scraper_tables(database_url)

    command.upgrade(config, "head")
    _assert_scraper_tables_absent_and_document_intact(database_url, row)
    _assert_canonical_fixture_reimport_is_unchanged(database_url, row)


def test_retirement_refuses_active_legacy_import_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'active-nice-job.db'}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))

    command.upgrade(config, PRE_PROVENANCE_REVISION)
    _insert_active_legacy_job(database_url)
    command.upgrade(config, PROVENANCE_REVISION)

    with pytest.raises(RuntimeError, match="while an import job is active"):
        command.upgrade(config, "0005_drop_nice_scraper_tables")

    engine = sa.create_engine(database_url)
    try:
        assert {"nice_download", "nice_import_job", "nice_import_item"} <= set(
            sa.inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_retirement_refuses_cached_guidance_without_a_materialized_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'cache-without-document.db'}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    row = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    command.upgrade(config, PRE_PROVENANCE_REVISION)
    _insert_legacy_nice_materialization(database_url, row)
    command.upgrade(config, PROVENANCE_REVISION)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM document WHERE id = 1"))
    engine.dispose()

    with pytest.raises(RuntimeError, match="cached guidance lacks a materialized document"):
        command.upgrade(config, "0005_drop_nice_scraper_tables")


@pytest.mark.parametrize(
    ("tamper_sql", "parameters"),
    [
        (
            "UPDATE nice_download SET content = :content WHERE id = 1",
            {"content": "tampered cached source"},
        ),
        (
            "UPDATE document SET content = :content WHERE id = 1",
            {"content": "tampered materialized source"},
        ),
        (
            "UPDATE document SET source_content_hash = :content_hash WHERE id = 1",
            {"content_hash": "0" * 64},
        ),
        (
            "UPDATE document SET source_metadata = :metadata WHERE id = 1",
            {"metadata": json.dumps({"legacy_cache": False})},
        ),
    ],
    ids=("cache-content", "document-content", "content-hash", "provenance-metadata"),
)
def test_retirement_refuses_tampered_cache_document_hash_or_provenance_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper_sql: str,
    parameters: dict[str, str],
) -> None:
    database_url = f"sqlite:///{tmp_path / f'tampered-{uuid4()}.db'}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    row = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    command.upgrade(config, PRE_PROVENANCE_REVISION)
    _insert_legacy_nice_materialization(database_url, row)
    command.upgrade(config, PROVENANCE_REVISION)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text(tamper_sql), parameters)
    engine.dispose()

    with pytest.raises(RuntimeError, match="cache/provenance evidence failed"):
        command.upgrade(config, "0005_drop_nice_scraper_tables")

    engine = sa.create_engine(database_url)
    try:
        assert {"nice_download", "nice_import_job", "nice_import_item"} <= set(
            sa.inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def _insert_legacy_nice_materialization(database_url: str, row: dict) -> None:
    now = datetime.now(timezone.utc)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO dataset (
                    id, name, display_name, eval_type, double_rate, trap_rate,
                    is_active, created_at, updated_at
                ) VALUES (1, 'legacy-nice', 'Legacy NICE', 'RETRIEVAL', 0, 0, 1, :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO document (
                    id, dataset_id, external_id, title, content, paragraphs,
                    metadata, is_active, created_at, updated_at
                ) VALUES (
                    1, 1, :external_id, :title, :content, :paragraphs,
                    NULL, 1, :now, :now
                )
                """
            ),
            {
                "external_id": row["external_id"],
                "title": row["title"],
                "content": row["content"],
                "paragraphs": json.dumps([{"idx": 0, "text": row["content"]}]),
                "now": now,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO nice_download (
                    id, reference, slug, title, page_url, pdf_url, page_count,
                    char_count, content, requested_by_user_id, created_at, updated_at
                ) VALUES (
                    1, :reference, :slug, :title, :page_url, :pdf_url, :page_count,
                    :char_count, :content, NULL, :now, :now
                )
                """
            ),
            {
                "reference": row["metadata"]["ref"],
                "slug": row["metadata"]["slug"],
                "title": row["title"],
                "page_url": row["url"],
                "pdf_url": f"{row['url']}/pdf",
                "page_count": row["section_count"],
                "char_count": len(row["content"]),
                "content": row["content"],
                "now": now,
            },
        )
    engine.dispose()


def _assert_provenance_backfill_evidence(database_url: str, row: dict) -> None:
    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        document = (
            connection.execute(
                sa.text(
                    """
                SELECT content, paragraphs, source, source_url, source_metadata,
                       source_content_hash
                FROM document WHERE id = 1
                """
                )
            )
            .mappings()
            .one()
        )
        assert document["content"] == row["content"]
        assert json.loads(document["paragraphs"]) == [
            {"idx": 0, "text": row["content"]}
        ]
        assert document["source"] == "nice"
        assert document["source_url"] == row["url"]
        assert _json_object(document["source_metadata"]) == {
            "legacy_cache": True,
            "ref": row["metadata"]["ref"],
            "slug": row["metadata"]["slug"],
        }
        assert document["source_content_hash"] == _content_hash(row["content"])
        assert (
            connection.execute(sa.text("SELECT count(*) FROM document")).scalar_one()
            == 1
        )
    engine.dispose()


def _assert_scraper_tables_absent_and_document_intact(
    database_url: str, row: dict
) -> None:
    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)
    assert not {"nice_download", "nice_import_job", "nice_import_item"} & set(
        inspector.get_table_names()
    )
    with engine.connect() as connection:
        document = connection.execute(
            sa.text("SELECT content, source_content_hash FROM document WHERE id = 1")
        ).one()
    assert document == (row["content"], _content_hash(row["content"]))
    engine.dispose()


def _assert_canonical_fixture_reimport_is_unchanged(
    database_url: str, row: dict
) -> None:
    """Exercise the frozen producer artifact after the cache tables are gone."""
    engine = sa.create_engine(database_url)
    try:
        with Session(engine) as session:
            dataset = session.get(Dataset, 1)
            assert dataset is not None
            result = import_scraped_document(
                session,
                dataset=dataset,
                row=parse_scraped_document_row(row),
                dry_run=False,
            )
            assert result.status == "unchanged"
            assert (
                session.execute(sa.text("SELECT count(*) FROM document")).scalar_one()
                == 1
            )
            assert (
                session.execute(sa.text("SELECT count(*) FROM chunk")).scalar_one() == 0
            )
    finally:
        engine.dispose()


def _assert_safe_downgrade_recreates_empty_scraper_tables(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)
    names = {"nice_download", "nice_import_job", "nice_import_item"}
    assert names <= set(inspector.get_table_names())
    with engine.connect() as connection:
        for table_name in names:
            assert (
                connection.execute(
                    sa.text(f"SELECT count(*) FROM {table_name}")
                ).scalar_one()
                == 0
            )
    engine.dispose()


def _insert_active_legacy_job(database_url: str) -> None:
    now = datetime.now(timezone.utc)
    user_id = uuid4().hex
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO "user" (
                    id, email, is_active, is_superuser, hashed_password,
                    created_at, updated_at
                ) VALUES (:id, 'legacy-job@example.com', 1, 0, 'hash', :now, :now)
                """
            ),
            {"id": user_id, "now": now},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO nice_import_job (
                    id, status, requested_limit, target_count, completed_count,
                    failed_count, started_by_user_id, active_slot, created_at, updated_at
                ) VALUES (1, 'pending', '10', NULL, 0, 0, :user_id, 1, :now, :now)
                """
            ),
            {"user_id": user_id, "now": now},
        )
    engine.dispose()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _json_object(value: object) -> dict:
    return json.loads(value) if isinstance(value, str) else value


def _backup_sqlite_database(source: Path, backup: Path) -> None:
    """Create a SQLite backup using SQLite's backup API, not a file copy."""
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(backup) as backup_connection,
    ):
        source_connection.backup(backup_connection)


def _restore_sqlite_database(backup: Path, destination: Path) -> None:
    """Restore a SQLite backup using the same database-native mechanism."""
    with (
        sqlite3.connect(backup) as backup_connection,
        sqlite3.connect(destination) as destination_connection,
    ):
        backup_connection.backup(destination_connection)


def _tamper_post_backup_title(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE document SET title = 'changed after backup' WHERE id = 1")
        )
    engine.dispose()
